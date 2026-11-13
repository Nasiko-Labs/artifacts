"""A2A executor (a2a-sdk 1.x), stateless: every message runs one fresh turn and the task always ends
`completed` (or `failed` on a platform error). Nothing pauses, nothing is resumed and nothing is stored: the
task store is in-memory and only lives for the request; earlier turns come from the platform's history slice.
The stored task (if a client reuses a task id) is never re-enqueued."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

import inputs
from agent import BriefAgent, TurnDeps
from llm import Llm
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST plain text part, history prefix removed. The platform appends
    org policy and agent knowledge as later, unmarked text parts; those are never the request."""
    return inputs.read_message(context.message).text


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


class BriefExecutor(AgentExecutor):
    def __init__(self, agent: BriefAgent, llm, model: str, clock: Callable[[], float] = time.monotonic,
                 deadline_secs: float | None = None):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._clock = clock
        self._deadline_secs = deadline_secs

    def _turn_llm(self, traceparent: str | None) -> Llm:
        llm = Llm(self._llm, self._model, traceparent, clock=self._clock)
        if self._deadline_secs is not None:
            llm.deadline_secs = self._deadline_secs
        return llm

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        incoming = inputs.read_message(context.message)
        deps = TurnDeps(llm=self._turn_llm(inbound_traceparent(context)), org=incoming.org)
        tracer = trace.get_tracer("exec-brief-writer-stateless")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("exec-brief-writer-stateless.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(incoming, task.context_id, deps)
            except Exception as e:
                logger.exception("exec brief turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            span.set_attribute("exec_brief.llm_calls", deps.llm.calls)
            await _apply(updater, result.reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, reply: str) -> None:
    """Always `completed`: the stateless agent never leaves a task in `input-required`."""
    parts = [new_text_part(reply)]
    await updater.add_artifact(parts, name="exec_brief")
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
