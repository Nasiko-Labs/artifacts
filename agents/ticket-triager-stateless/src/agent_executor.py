"""A2A executor (a2a-sdk 1.x), stateless: every message runs the chain once from the message alone (its
history included) and ends the task `completed` (or `failed` on an unexpected error). Nothing pauses, and
no task or chain state outlives the request, so any replica can answer any message.
"""

from __future__ import annotations

import logging
import time

from a2a.helpers import (
    new_data_part,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from chain import TicketTriagerAgent, TurnResult
from inputs import read_message
from llm import TURN_DEADLINE_SECS, TurnDeps
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only, after the last history marker. The platform appends
    org policy and agent knowledge as later, unmarked text parts; those are never the request."""
    return read_message(context.message).text


def inbound_traceparent(context: RequestContext) -> str | None:
    """This request's traceparent: the header a2a-sdk recorded, else the one TraceparentMiddleware
    extracted for this request. Read per request, never cached."""
    header = extract_traceparent(context)
    if header:
        return header
    extracted = request_otel_context.get()
    if extracted is None or not trace.get_current_span(extracted).get_span_context().is_valid:
        return None
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier, context=extracted)
    return carrier.get("traceparent")


class TicketTriagerExecutor(AgentExecutor):
    def __init__(self, agent: TicketTriagerAgent, llm, model: str, deadline_secs: float = TURN_DEADLINE_SECS):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._deadline_secs = deadline_secs

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, traceparent=inbound_traceparent(context),
                        deadline=time.monotonic() + self._deadline_secs)
        incoming = read_message(context.message)
        tracer = trace.get_tracer("ticket-triager")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("ticket-triager.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(incoming, deps)
            except Exception as e:
                logger.exception("ticket-triager turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            span.set_attribute("ticket_triager.llm_calls", deps.budget.calls)
            await _apply(updater, result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    text = [new_text_part(result.reply)]
    parts = [*text, new_data_part(result.data)] if result.data else text
    await updater.add_artifact(parts, name="triage")
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
