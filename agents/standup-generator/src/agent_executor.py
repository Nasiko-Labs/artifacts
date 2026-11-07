"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; the answer to the one scope question
("Which repos or project?") resumes from the pause kept in the task's own metadata. The stored task is
never re-enqueued. A finished standup's artifact carries two parts: the markdown (or the fenced JSON
envelope when asked) and a data part with the full envelope."""

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

import prompts
from deps import TurnDeps
from graph import StandupAgent, TurnResult
from nasiko_hitl import question_metadata
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
PENDING_KEY = "standup_pending"
ANY_SCOPE_DESCRIPTION = "Read whatever the connected code host and tracker return for these people."
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The records
# live in the checkpointer, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are never part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def current_message(raw: str) -> str:
    idx = raw.rfind(_CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(_CURRENT_MESSAGE_MARKER):]


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


class StandupExecutor(AgentExecutor):
    def __init__(self, agent: StandupAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        tracer = trace.get_tracer("standup-generator")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("standup_generator.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._run(stored, text, task.context_id, deps)
            except Exception as e:
                logger.exception("standup turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, result)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps) -> TurnResult:
        if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            # The paused task's metadata round-trips as a protobuf Struct.
            pending = MessageToDict(stored.metadata).get(PENDING_KEY)
            if not isinstance(pending, dict):  # fail closed: never guess what was asked
                return TurnResult(prompts.NOT_WAITING)
            return await self._agent.resume(pending, text, context_id, deps)
        return await self._agent.turn(text, context_id, deps)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    text = [new_text_part(result.reply)]
    if result.pause is None:
        parts = [*text, new_data_part(result.data)] if result.data else text
        await updater.add_artifact(parts, name="standup")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
        return
    # The question goes on the status message (the platform builds the request card from
    # `status.message.metadata`); what to resume from goes on the task's metadata.
    question = question_metadata(prompts.SCOPE_HEADER, [(prompts.ANY_SCOPE, ANY_SCOPE_DESCRIPTION)],
                                 allow_custom_input=True)
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message(text, metadata=question),
                                metadata={PENDING_KEY: result.pause})
