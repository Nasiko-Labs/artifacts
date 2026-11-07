"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes from the
task's own metadata (bug pick, ticket pick, or the approve → send walk). The stored task is never
re-enqueued."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from a2a.helpers import get_text_parts, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

import pick
import replies
import walk
from budget import BudgetedLLM
from deps import TurnDeps
from graph import NotifierAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
_PAUSE_KINDS = {"input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
                "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED}
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are instructions, never the request."""
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


class NotifierExecutor(AgentExecutor):
    def __init__(self, agent: NotifierAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now
        self._clock = clock

    def _deps(self, context: RequestContext) -> TurnDeps:
        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), now=self._now())
        budgeted = BudgetedLLM(self._llm, max_calls=deps.config.turn_llm_calls,
                               deadline_secs=deps.config.turn_deadline_secs, clock=self._clock)
        return TurnDeps(llm=budgeted, model=deps.model, gateway=deps.gateway, traceparent=deps.traceparent,
                        now=deps.now, config=deps.config)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        deps = self._deps(context)
        text = current_message(user_text(context))
        tracer = trace.get_tracer("bug-to-customer-notifier")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("bug_notifier.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, text, task.context_id, deps)
            except Exception as e:
                logger.exception("bug notifier turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps) -> walk.Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            phase = MessageToDict(stored.metadata).get(pick.PHASE_KEY)
            outcome = await self._resume(phase if isinstance(phase, Mapping) else {}, text, context_id, deps)
        else:
            outcome = _from_result(await self._agent.turn(text, context_id, deps))
        if outcome.outcomes:
            await self._agent.record_outcomes(context_id, outcome.outcomes)
        return outcome

    async def _resume(self, phase: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps) -> walk.Outcome:
        kind = phase.get("phase")
        if kind == "walk":
            return await walk.resume(deps, phase, text)
        if kind == "pick_bug":
            key = pick.chosen_bug(text, [str(k) for k in phase.get("keys") or ()])
            if key is None:
                return walk.Outcome("complete", replies.UNKNOWN_BUG_CHOICE)
            return _from_result(await self._agent.turn(text, context_id, deps, entry="ship", selected_key=key))
        if kind == "pick_tickets":
            offered = {str(k): str(v) for k, v in dict(phase.get("offered") or {}).items()}
            ids, instruction = pick.chosen_tickets(text, offered, deps.config.max_instruction_chars)
            if not ids:
                return walk.Outcome("complete", replies.NOTHING_SELECTED)
            return _from_result(await self._agent.turn(text, context_id, deps, entry="draft", draft_ids=ids,
                                                       instruction=instruction))
        return walk.Outcome("complete", walk.NOT_WAITING)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _from_result(result: TurnResult) -> walk.Outcome:
    if result.pause is not None:
        return walk.Outcome("input_required", result.pause.text, result.pause.metadata, result.pause.hitl)
    if result.drafts and result.facts is not None:
        return walk.start(result.drafts, result.facts, result.reply)
    return walk.Outcome("complete", result.reply)


async def _apply_outcome(updater: TaskUpdater, outcome: walk.Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.kind == "complete":
        await updater.add_artifact(parts, name="bug_to_customer_notifier")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    # The pause keys go on the status message: the platform builds the question from
    # `status.message.metadata` and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
