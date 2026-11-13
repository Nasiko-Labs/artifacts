"""A2A executor (a2a-sdk 1.x), stateless: every message runs the turn once and its task ends `completed`
(or `failed` when a platform call breaks). Nothing pauses, so no task waits in `input-required`, and nothing
is read from or written to any store beyond this request.

A finished draft's artifact carries two parts: the markdown (or fenced JSON when asked) for people and
MAF steps, and a data part with the full envelope for API clients."""

from __future__ import annotations

import logging
import time

from a2a.helpers import (
    get_text_parts,
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

from flow import TurnResult, WriterAgent
from inputs import read_message
from llm import TURN_DEADLINE_SECS, TurnDeps
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; joining them would make them part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


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


class WriterExecutor(AgentExecutor):
    def __init__(self, agent: WriterAgent, llm, model: str, deadline_secs: float = TURN_DEADLINE_SECS):
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
        tracer = trace.get_tracer("pr-description-writer-stateless")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("pr-description-writer-stateless.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(incoming, deps)
            except Exception as e:
                logger.exception("pr-description-writer-stateless turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            span.set_attribute("pr_description_writer_stateless.llm_calls", deps.budget.calls)
            await _apply(updater, result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    text = [new_text_part(result.reply)]
    parts = [*text, new_data_part(result.data)] if result.data else text
    await updater.add_artifact(parts, name="pr-description")
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
