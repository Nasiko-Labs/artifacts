"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the sheet pick or
the column pick from the task's own metadata. The stored task is never re-enqueued. An audited turn also
carries the envelope as a DataPart. This agent is read-only: there is no approval card.
(Pattern copied from the hardened kit; owned by this agent.)"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
from graph import COLUMN_KEY, SHEET_KEY, TrustAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NO_PICK_TEXT = "OK, nothing read. Name the sheet (\"audit sheet <name>\") or paste the table."
NOT_WAITING = "I'm not waiting on an answer for that. Ask me to audit a dashboard inventory."
NOTHING_TO_SAY = "Done."
ARTIFACT_NAME = "dashboard_trust_audit"
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The audit lives
# in the checkpointer, so only the current message is used (earlier replies stay out of prompts).
_CURRENT_MESSAGE_MARKER = "Current message: "


@dataclass(frozen=True)
class Outcome:
    kind: str                           # "complete" | "input_required"
    text: str
    metadata: Mapping[str, Any] | None = None
    hitl: Mapping[str, Any] | None = None
    envelope: Mapping[str, Any] | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; joining them would make them part of the request."""
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


class TrustExecutor(AgentExecutor):
    def __init__(self, agent: TrustAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
                        traceparent=inbound_traceparent(context), now=self._now(), started=self._clock(),
                        clock=self._clock)
        text = current_message(user_text(context))
        tracer = trace.get_tracer("dashboard-trust-checker")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("trust_checker.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, text, task.context_id, deps, _message_metadata(context))
            except Exception as e:
                logger.exception("dashboard-trust-checker turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps,
                   message_metadata: Mapping[str, Any]) -> Outcome:
        if stored is None or stored.status.state not in _PAUSED_STATES:
            return _outcome_of(await self._agent.turn(text, context_id, deps, message_metadata))
        # The paused task's metadata round-trips as a protobuf Struct.
        metadata = MessageToDict(stored.metadata)
        if isinstance(metadata.get(SHEET_KEY), Mapping):
            return await self._sheet_picked(metadata[SHEET_KEY], text, context_id, deps, message_metadata)
        if isinstance(metadata.get(COLUMN_KEY), Mapping):
            return await self._column_picked(metadata[COLUMN_KEY], text, context_id, deps, message_metadata)
        return Outcome("complete", NOT_WAITING)

    async def _sheet_picked(self, pick: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps,
                            message_metadata: Mapping[str, Any]) -> Outcome:
        title = _matching(pick.get("names"), text)
        if title is None:
            return Outcome("complete", NO_PICK_TEXT)
        query = str(pick.get("query") or text)
        return _outcome_of(await self._agent.turn(query, context_id, deps, message_metadata, pick={"title": title}))

    async def _column_picked(self, pick: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps,
                             message_metadata: Mapping[str, Any]) -> Outcome:
        column = _matching(pick.get("names"), text)
        if column is None:
            return Outcome("complete", prompts.HEADER_PICK_FAILED)
        chosen = {"field": str(pick.get("field") or ""), "column": column}
        return _outcome_of(await self._agent.turn(text, context_id, deps, message_metadata, pick=chosen))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _matching(names: Any, text: str) -> str | None:
    options = [str(n) for n in names] if isinstance(names, list) else []
    return next((n for n in options if n.strip().casefold() == text.strip().casefold()), None)


def _outcome_of(result: TurnResult) -> Outcome:
    question = result.question
    if question is not None:
        text = "\n\n".join(p for p in (result.reply, question.get("text")) if p)
        return Outcome("input_required", text, question.get("metadata"), question.get("hitl"), result.envelope)
    return Outcome("complete", result.reply or NOTHING_TO_SAY, envelope=result.envelope)


def _message_metadata(context: RequestContext) -> dict:
    message = context.message
    if message is None or not message.HasField("metadata"):
        return {}
    return MessageToDict(message.metadata)


async def _apply_outcome(updater: TaskUpdater, outcome: Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.kind == "complete":
        extra = [new_data_part(dict(outcome.envelope))] if outcome.envelope is not None else []
        await updater.add_artifact([*parts, *extra], name=ARTIFACT_NAME)
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=dict(outcome.metadata) if outcome.metadata else None)
        return
    # The pause keys go on the status message: the platform builds the question from
    # `status.message.metadata`.
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message(parts, metadata=dict(outcome.hitl or {})),
                                metadata=dict(outcome.metadata or {}))
