"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; the answer to the one repository question resumes
it. The stored task is never re-enqueued. A finished turn's artifact carries two parts: the markdown (or the
fenced JSON envelope when asked) and a data part with the full envelope."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

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

from deps import TurnDeps
from graph import BlastRadiusAgent, TurnResult
from intake import current_message, read_message
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
ARTIFACT_NAME = "terraform_blast_radius"


def utc_now() -> datetime:
    return datetime.now(UTC)


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


class BlastRadiusExecutor(AgentExecutor):
    def __init__(self, agent: BlastRadiusAgent, llm, model: str, gateway, now: Callable[[], datetime] = utc_now):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), now=self._now())
        tracer = trace.get_tracer("terraform-blast-radius")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("terraform_blast_radius.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
                    result = await self._agent.resume(current_message(user_text(context)), task.context_id, deps)
                else:
                    result = await self._agent.turn(read_message(context.message), task.context_id, deps)
            except Exception as e:
                logger.exception("terraform blast radius turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            span.set_attribute("terraform_blast_radius.llm_calls", deps.budget.calls)
            await _apply(updater, result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    text = [new_text_part(result.reply)]
    if result.pause:
        # The question goes on the status message: the platform builds the request card from it.
        await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                    message=updater.new_agent_message(text, metadata=result.pause))
        return
    parts = [*text, new_data_part(result.data)] if result.data else text
    await updater.add_artifact(parts, name=ARTIFACT_NAME)
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
