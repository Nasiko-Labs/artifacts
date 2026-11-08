"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to the "Which IaC file?" pause resumes
using the message kept in the task metadata. The stored task is never re-enqueued.

A finished report's artifact carries two parts: the markdown (or fenced JSON when asked) for people and
MAF steps, and a data part with the full envelope for API clients."""

from __future__ import annotations

import logging
import time
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
from google.protobuf.json_format import MessageToDict
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

import pauses
from deps import TurnDeps
from graph import DriftAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The report
# lives in the checkpointer, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "Current message: "


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are never part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def current_message(raw: str) -> str:
    """The text after the LAST `Current message:` marker (at the start of a line), else all of it."""
    idx = raw.rfind("\n" + _CURRENT_MESSAGE_MARKER)
    if idx != -1:
        return raw[idx + 1 + len(_CURRENT_MESSAGE_MARKER):]
    return raw.removeprefix(_CURRENT_MESSAGE_MARKER)


def wants_json_metadata(context: RequestContext) -> bool:
    message = context.message
    if message is None or not message.HasField("metadata"):
        return False
    return str(MessageToDict(message.metadata).get("output") or "").casefold() == "json"


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


class DriftExecutor(AgentExecutor):
    def __init__(self, agent: DriftAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now
        self._clock = clock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), now=self._now(), clock=self._clock,
                        started=self._clock())
        text = current_message(user_text(context))
        tracer = trace.get_tracer("config-drift-detector")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("config_drift_detector.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._run(stored, text, task.context_id, deps, wants_json_metadata(context))
            except Exception as e:
                logger.exception("config drift turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
        await _apply(updater, result)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps, wants_json: bool) -> TurnResult:
        if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            # The paused task's metadata round-trips as a protobuf Struct.
            pending = pauses.Pending.from_metadata(MessageToDict(stored.metadata))
            if pending is None:  # fail closed: never guess what was asked
                return TurnResult(NOT_WAITING)
            return await self._agent.resume(pending, text, context_id, deps)
        return await self._agent.turn(text, context_id, deps, wants_json)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    if result.pause is None:
        text = [new_text_part(result.reply)]
        parts = [*text, new_data_part(result.data)] if result.data else text
        await updater.add_artifact(parts, name="config_drift_detector")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
        return
    # The question goes on the status message (the platform builds the request card from
    # `status.message.metadata`); what to resume from goes on the task's metadata.
    text, question = pauses.question()
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message([new_text_part(text)], metadata=question),
                                metadata=result.pause.to_metadata())
