"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the
approve → send walk, the "which address?" question or the "which reminders?" pick, from the task's own
metadata. The stored task is never re-enqueued. The turn is bounded by TURN_DEADLINE_SECS."""

from __future__ import annotations

import asyncio
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

import drafting
import picker
import prompts
import walk
from deps import TurnDeps
from drafts import interpret_answer
from graph import ChaserAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

TURN_DEADLINE_SECS = 300
FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NO_PICK_TEXT = "OK, I won't draft any reminders. Say \"draft the next reminder for #2\" (any number) whenever you want one."
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


class ChaserExecutor(AgentExecutor):
    def __init__(self, agent: ChaserAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic,
                 deadline_secs: float = TURN_DEADLINE_SECS):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now
        self._clock = clock
        self._deadline = deadline_secs

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
        tracer = trace.get_tracer("invoice-chaser")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("invoice_chaser.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await asyncio.wait_for(self._run(stored, text, task.context_id, deps), self._deadline)
            except TimeoutError:
                logger.warning("turn passed its %ss deadline", self._deadline)
                outcome = walk.Outcome("complete", prompts.TIMED_OUT)
            except Exception as e:
                logger.exception("invoice-chaser turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps) -> walk.Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct and accumulates across pauses:
            # the newest pause wins (walk, then the address question, then the pick).
            metadata = MessageToDict(stored.metadata)
            address = metadata.get(drafting.ADDRESS_KEY)
            pick = metadata.get(picker.METADATA_KEY)
            if walk.METADATA_KEY in metadata:
                outcome = await walk.resume(deps, metadata, text)
            elif isinstance(address, Mapping):
                outcome = await self._addressed(address, text, context_id, deps)
            elif isinstance(pick, Mapping):
                outcome = await self._picked(pick, text, context_id, deps)
            else:
                outcome = await walk.resume(deps, metadata, text)
        else:
            outcome = await _outcome_of(deps, await self._agent.turn(text, context_id, deps))
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

    async def _addressed(self, ask: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps) -> walk.Outcome:
        """The address answer: a typed address (sendable only if seen in tool output) or copy only."""
        ids = [str(i) for i in ask.get("ids") or ()]
        answered = ask.get("answered") if isinstance(ask.get("answered"), Mapping) else {}
        addresses = {**{str(k): (str(v) if v else None) for k, v in answered.items()},
                     str(ask.get("key") or ""): drafting.typed_address(text)}
        result = await self._agent.draft_named(ids, str(ask.get("instruction") or ""), context_id, deps, addresses)
        return await _outcome_of(deps, result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _outcome_of(deps: TurnDeps, result: TurnResult) -> walk.Outcome:
    if result.drafts:
        return await walk.start(deps, result.drafts, result.reply)
    if result.address is not None:
        text = "\n\n".join(p for p in (result.reply, result.address.text) if p)
        return walk.Outcome("input_required", text, result.address.metadata, result.address.hitl)
    if result.pick is not None:
        text = "\n\n".join(p for p in (result.reply, result.pick.text) if p)
        return walk.Outcome("input_required", text, result.pick.metadata, result.pick.hitl, data=result.envelope)
    return walk.Outcome("complete", result.reply or picker.NOTHING_PICKED, data=result.envelope)


async def _apply_outcome(updater: TaskUpdater, outcome: walk.Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.data is not None:
        # The reply as text (markdown, or the envelope as a fenced json block), plus the envelope for API clients.
        await updater.add_artifact([*parts, new_data_part(outcome.data)], name="invoice_chaser")
    if outcome.kind == "complete":
        if outcome.data is None:
            await updater.add_artifact(parts, name="invoice_chaser")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    # The pause keys go on the status message: the platform builds the question from
    # `status.message.metadata` and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
