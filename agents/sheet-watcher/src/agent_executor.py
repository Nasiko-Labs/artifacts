"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the sheet
pick, the key-column pick, the channel pick, or the approve -> post walk, from the task's own metadata. The
stored task is never re-enqueued. A check also carries the envelope as a DataPart.
(Pattern copied from the hardened kit; owned by this agent.)"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
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

import post
from deps import TurnDeps
from graph import KEY_KEY, SHEET_KEY, TurnResult, WatcherAgent
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NO_PICK_TEXT = "OK, nothing read. Name the sheet (\"watch the <sheet name>\") or paste the table."
NO_KEY_TEXT = "OK, nothing compared. Say \"key is <column>\" with the column that identifies a row, then ask again."
NO_CHANNEL_TEXT = "OK, nothing posted. Say \"post it to #channel\" whenever you want it posted."
NOTHING_TO_SAY = "Done."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
_PAUSE_KINDS = {"input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
                "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED}
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The snapshot lives
# in the checkpointer, so only the current message is used (earlier replies stay out of prompts).
_CURRENT_MESSAGE_MARKER = "Current message: "


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


class WatcherExecutor(AgentExecutor):
    def __init__(self, agent: WatcherAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        tracer = trace.get_tracer("sheet-watcher")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("sheet_watcher.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, text, task.context_id, deps, _message_metadata(context))
            except Exception as e:
                logger.exception("sheet-watcher turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps,
                   message_metadata: Mapping[str, Any]) -> post.Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            metadata = MessageToDict(stored.metadata)
            if isinstance(metadata.get(post.WALK_KEY), Mapping):
                outcome = await post.resume(deps, metadata, text)
            elif isinstance(metadata.get(post.CHANNEL_KEY), Mapping):
                outcome = _channel_picked(metadata[post.CHANNEL_KEY], text)
            elif isinstance(metadata.get(SHEET_KEY), Mapping):
                outcome = await self._sheet_picked(metadata[SHEET_KEY], text, context_id, deps, message_metadata)
            elif isinstance(metadata.get(KEY_KEY), Mapping):
                outcome = await self._key_picked(metadata[KEY_KEY], text, context_id, deps, message_metadata)
            else:
                outcome = post.Outcome("complete", post.NOT_WAITING)
        else:
            outcome = _outcome_of(await self._agent.turn(text, context_id, deps, message_metadata))
        if outcome.remember:
            await self._agent.remember(context_id, outcome.remember)
        return outcome

    async def _sheet_picked(self, pick: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps,
                            message_metadata: Mapping[str, Any]) -> post.Outcome:
        names = {str(k): str(v) for k, v in (pick.get("names") or {}).items()}
        title = next((label for label in names if label.casefold() == text.strip().casefold()), None)
        if title is None:
            return post.Outcome("complete", NO_PICK_TEXT)
        query = str(pick.get("query") or text)
        result = await self._agent.turn(query, context_id, deps, message_metadata,
                                        pick={"id": names[title], "title": title})
        return _outcome_of(result)

    async def _key_picked(self, pick: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps,
                          message_metadata: Mapping[str, Any]) -> post.Outcome:
        options = [str(o) for o in pick.get("options") or ()]
        chosen = next((o for o in options if o.casefold() == text.strip().casefold()), None)
        if chosen is None:
            return post.Outcome("complete", NO_KEY_TEXT)
        sheet_pick = pick.get("pick") if isinstance(pick.get("pick"), Mapping) else None
        result = await self._agent.turn(str(pick.get("query") or ""), context_id, deps, message_metadata,
                                        pick=sheet_pick, key_pick=chosen)
        return _outcome_of(result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _channel_picked(pick: Mapping[str, Any], text: str) -> post.Outcome:
    if post.interpret(text)[0] == "skip":
        return post.Outcome("complete", NO_CHANNEL_TEXT)
    draft = post.picked_channel(pick, text)
    if draft is None or not draft.tool:
        return post.Outcome("complete", NO_CHANNEL_TEXT)
    return post.start(draft)


def _outcome_of(result: TurnResult) -> post.Outcome:
    if result.card is not None:
        return post.start(result.card, result.reply, result.envelope)
    question = result.question
    if question is not None:
        text = "\n\n".join(p for p in (result.reply, question.get("text")) if p)
        return post.Outcome("input_required", text, question.get("metadata"), question.get("hitl"),
                            envelope=result.envelope)
    return post.Outcome("complete", result.reply or NOTHING_TO_SAY, envelope=result.envelope)


def _message_metadata(context: RequestContext) -> dict:
    message = context.message
    if message is None or not message.HasField("metadata"):
        return {}
    return MessageToDict(message.metadata)


async def _apply_outcome(updater: TaskUpdater, outcome: post.Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.envelope is not None:  # the envelope for API clients; MAF reads only the text part
        await updater.add_artifact([*parts, new_data_part(outcome.envelope)], name="sheet_watch_events")
    if outcome.kind == "complete":
        if outcome.envelope is None:
            await updater.add_artifact(parts, name="sheet_watcher")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    # The pause keys go on the status message: the platform builds the question from
    # `status.message.metadata` and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
