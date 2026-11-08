"""A2A executor (a2a-sdk 1.x): every turn runs the graph and completes. There are no pauses: the agent
is read-only and has no write path, so nothing needs an approval. A collection's reply carries two
parts: the markdown packets and a DataPart with the envelope (in JSON mode, the envelope as a fenced
json block in the text part)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

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

from deps import TurnDeps
from graph import KudosAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from request import from_envelope, wants_json
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
ARTIFACT_NAME = "kudos_evidence"
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The packets
# live in the checkpointer, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "Current message: "


def utc_today() -> date:
    return datetime.now(UTC).date()


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are never part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def current_message(raw: str) -> str:
    idx = raw.rfind(_CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(_CURRENT_MESSAGE_MARKER):]


def json_requested(context: RequestContext, text: str) -> bool:
    message = context.message
    metadata = MessageToDict(message.metadata) if message is not None and message.HasField("metadata") else {}
    return str(metadata.get("output") or "").casefold() == "json" or wants_json(text)


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


def reply_parts(result: TurnResult, as_json: bool) -> tuple[list, list]:
    """(artifact parts, status message parts)."""
    if result.envelope is None:
        parts = [new_text_part(result.reply)]
        return parts, parts
    text = "```json\n" + json.dumps(result.envelope, indent=1) + "\n```" if as_json else result.reply
    parts = [new_text_part(text)]
    return [*parts, new_data_part(result.envelope)], parts


class KudosExecutor(AgentExecutor):
    def __init__(self, agent: KudosAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 today: Callable[[], date] = utc_today, clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._today = today
        self._clock = clock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), today=self._today(), clock=self._clock,
                        started=self._clock())
        text = from_envelope(current_message(user_text(context)))
        as_json = json_requested(context, text)
        tracer = trace.get_tracer("kudos-collector")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("kudos_collector.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(text, task.context_id, deps)
            except Exception as e:
                logger.exception("kudos collector turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
        artifact, message = reply_parts(result, as_json)
        await updater.add_artifact(artifact, name=ARTIFACT_NAME)
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(message))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
