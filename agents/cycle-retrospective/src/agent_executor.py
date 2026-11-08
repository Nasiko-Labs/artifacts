"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the "Which
team?" pick or the post card from the task's own metadata. The stored task is never re-enqueued. A retro
reply carries two parts: the markdown and a DataPart with the envelope (in JSON mode, the envelope as a
fenced json block, and never a question)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
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

import post
import replies
from budget import BudgetedLLM
from deps import TurnDeps
from graph import RetroAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from request import from_envelope, wants_json
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
_PAUSE_KINDS = {"input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
                "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED}
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns.
_CURRENT_MESSAGE_MARKER = "Current message: "


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


class RetroExecutor(AgentExecutor):
    def __init__(self, agent: RetroAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now
        self._clock = clock

    def _deps(self, context: RequestContext) -> TurnDeps:
        base = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), now=self._now())
        budgeted = BudgetedLLM(self._llm, max_calls=base.config.turn_llm_calls,
                               deadline_secs=base.config.turn_deadline_secs, clock=self._clock)
        return TurnDeps(llm=budgeted, model=base.model, gateway=base.gateway, traceparent=base.traceparent,
                        now=base.now, config=base.config)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        deps = self._deps(context)
        text = from_envelope(current_message(user_text(context)))
        as_json = json_requested(context, text)
        tracer = trace.get_tracer("cycle-retrospective")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("cycle_retro.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, text, task.context_id, deps, as_json)
            except Exception as e:
                logger.exception("cycle retro turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps, as_json: bool) -> post.Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            phase = MessageToDict(stored.metadata).get(post.PHASE_KEY)
            phase = phase if isinstance(phase, Mapping) else {}
            if phase.get("phase") == "post":
                return await post.resume_post(deps, phase, text)
            if phase.get("phase") == "team":
                chosen = post.chosen_team(text, [str(n) for n in phase.get("teams") or ()])
                if chosen is None:
                    return post.Outcome("complete", replies.UNKNOWN_TEAM)
                result = await self._agent.turn(str(phase.get("query") or ""), context_id, deps, team_choice=chosen)
                return _outcome_of(result, as_json)
            return post.Outcome("complete", post.NOT_WAITING)
        return _outcome_of(await self._agent.turn(text, context_id, deps), as_json)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _outcome_of(result: TurnResult, as_json: bool) -> post.Outcome:
    data = {"envelope": result.envelope} if result.envelope else None
    if as_json and result.envelope:  # a workflow step: the envelope as text, and never a question
        return post.Outcome("complete", "```json\n" + json.dumps(result.envelope, indent=1) + "\n```", data=data)
    if result.pause is not None:
        return post.Outcome("input_required", result.pause.text, result.pause.metadata, result.pause.hitl, data)
    return post.Outcome("complete", result.reply or replies.UNREADABLE_ANSWER, data=data)


async def _apply_outcome(updater: TaskUpdater, outcome: post.Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    envelope = (outcome.data or {}).get("envelope")
    if outcome.kind == "complete":
        await updater.add_artifact([*parts, new_data_part(envelope)] if envelope else parts, name="cycle_retro")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    if envelope:  # the retro's data travels with the pause, for API clients
        await updater.add_artifact([new_data_part(envelope)], name="cycle_retro")
    # The pause keys go on the status message: the platform builds the question from
    # `status.message.metadata` and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
