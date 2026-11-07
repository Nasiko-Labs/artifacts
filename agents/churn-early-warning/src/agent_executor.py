"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the
"which accounts?" pick, the missing-address question, or the approve → send walk, from the task's own
metadata. The stored task is never re-enqueued. A list reply carries two parts: the markdown and a
DataPart with the envelope (or, in JSON mode, the envelope as a fenced json block and no question)."""

from __future__ import annotations

import json
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

import picker
import walk
from deps import TurnDeps
from drafts import interpret_answer
from graph import ChurnAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from request import wants_json
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NO_PICK_TEXT = "OK, I won't draft any outreach. Say \"draft outreach for #2\" (or any number) whenever you want one."
NO_EMAIL_TEXT = "OK, I won't draft it."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
_PAUSE_KINDS = {"input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
                "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED}
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The list
# lives in the checkpointer, so only the current message is used (earlier replies stay out of prompts).
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


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


class ChurnExecutor(AgentExecutor):
    def __init__(self, agent: ChurnAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        as_json = json_requested(context, text)
        tracer = trace.get_tracer("churn-early-warning")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("churn.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, text, task.context_id, deps, as_json)
            except Exception as e:
                logger.exception("churn turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps, as_json: bool) -> walk.Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct. It accumulates across pauses:
            # once the walk has started, the walk wins.
            metadata = MessageToDict(stored.metadata)
            if walk.METADATA_KEY in metadata:
                outcome = await walk.resume(deps, metadata, text)
            elif isinstance(metadata.get(picker.EMAIL_KEY), Mapping):
                outcome = await self._addressed(metadata[picker.EMAIL_KEY], text, context_id, deps)
            elif isinstance(metadata.get(picker.METADATA_KEY), Mapping):
                outcome = await self._picked(metadata[picker.METADATA_KEY], text, context_id, deps)
            else:
                outcome = await walk.resume(deps, metadata, text)
        else:
            outcome = await _outcome_of(deps, await self._agent.turn(text, context_id, deps), as_json)
        if outcome.outcomes:
            await self._agent.record_outcomes(context_id, outcome.outcomes)
        return outcome

    async def _picked(self, pick: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps) -> walk.Outcome:
        """The multi-select answer: picked `#n` lines become drafts (other lines their instruction); no pick
        plus a skip word ends here; no pick plus other text is handled as a new message."""
        numbers = {str(k): str(v) for k, v in (pick.get("numbers") or {}).items()}
        keys, instruction = picker.read_answer(text, numbers)
        if keys:
            return await _outcome_of(deps, await self._agent.draft_picked(keys, instruction, context_id, deps))
        if interpret_answer(instruction)[0] == "skip":
            return walk.Outcome("complete", NO_PICK_TEXT)
        return await _outcome_of(deps, await self._agent.turn(instruction, context_id, deps))

    async def _addressed(self, asked: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps) -> walk.Outcome:
        """The missing-address answer: an address drafts to it; a skip word ends; other text is a new message."""
        email = picker.email_in(text)
        if email:
            result = await self._agent.draft_picked([str(asked.get("key") or "")], str(asked.get("instruction") or ""),
                                                    context_id, deps, typed_email=email)
            return await _outcome_of(deps, result)
        if interpret_answer(text)[0] == "skip":
            return walk.Outcome("complete", NO_EMAIL_TEXT)
        return await _outcome_of(deps, await self._agent.turn(text, context_id, deps))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _outcome_of(deps: TurnDeps, result: TurnResult, as_json: bool = False) -> walk.Outcome:
    if result.drafts:
        return await walk.start(deps, result.drafts, result.reply)
    data = {"envelope": result.envelope} if result.envelope else None
    if as_json and result.envelope:  # a workflow step: the envelope as text, and never a question
        return walk.Outcome("complete", "```json\n" + json.dumps(result.envelope, indent=1) + "\n```", data=data)
    if result.pick is not None:
        text = "\n\n".join(p for p in (result.reply, result.pick.text) if p)
        return walk.Outcome("input_required", text, result.pick.metadata, result.pick.hitl, data=data)
    return walk.Outcome("complete", result.reply or picker.NOTHING_PICKED, data=data)


async def _apply_outcome(updater: TaskUpdater, outcome: walk.Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    envelope = (outcome.data or {}).get("envelope")
    if outcome.kind == "complete":
        await updater.add_artifact([*parts, new_data_part(envelope)] if envelope else parts, name="churn_risk")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    if envelope:  # the list's data travels with the pause, for API clients
        await updater.add_artifact([new_data_part(envelope)], name="churn_risk")
    # The pause keys go on the status message: the platform builds the question from
    # `status.message.metadata` and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
