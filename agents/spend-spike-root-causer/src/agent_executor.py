"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the repo pick, the
target pick or the approve → post walk, from the task's own metadata. The stored task is never re-enqueued.

A finished analysis's artifact carries two parts: the markdown (or a fenced JSON envelope when asked) for people
and MAF steps, and a data part with the full envelope for API clients."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from a2a.helpers import get_text_parts, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from google.protobuf.json_format import MessageToDict
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

import graph
import picker
import walk
from deps import TurnDeps
from graph import SpikeAgent
from inputs import current_message, incoming, read_message
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NO_TARGET_PICKED = "Skipped: nothing was posted."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
_PAUSE_KINDS = {"input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
                "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED}


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only, after the last history marker. The platform appends org
    policy and agent knowledge as later, unmarked text parts; joining them would make them part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return current_message(texts[0]) if texts else ""


def inbound_traceparent(context: RequestContext) -> str | None:
    """This request's traceparent: the header a2a-sdk recorded, else the one TraceparentMiddleware extracted for
    this request. Read per request, never cached."""
    header = extract_traceparent(context)
    if header:
        return header
    extracted = request_otel_context.get()
    if extracted is None or not trace.get_current_span(extracted).get_span_context().is_valid:
        return None
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier, context=extracted)
    return carrier.get("traceparent")


class SpikeExecutor(AgentExecutor):
    def __init__(self, agent: SpikeAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
                        traceparent=inbound_traceparent(context), today=self._now().date(), clock=self._clock,
                        started=self._clock())
        tracer = trace.get_tracer("spend-spike-root-causer")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("spend_spike.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, context, task.context_id, deps)
            except Exception as e:
                logger.exception("spend-spike turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, context: RequestContext, context_id: str, deps: TurnDeps) -> walk.Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            phase = MessageToDict(stored.metadata).get(walk.PHASE_KEY)
            if isinstance(phase, Mapping):
                return await self._resume(phase, user_text(context), context_id, deps)
        message = read_message(context.message)
        return await self._agent.turn(message, context_id, deps)

    async def _resume(self, phase: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps) -> walk.Outcome:
        kind = phase.get("phase")
        if kind == "walk":
            return await walk.resume(deps, phase, text)
        if kind == "pick_repo":
            if walk.interpret_answer(text)[0] == "skip":
                return walk.Outcome("complete", "OK, deploy correlation was not checked.")
            return await self._agent.picked_repos(text, list(phase.get("offered") or []), context_id, deps)
        if kind == "pick_target":
            return await _picked_target(phase, text, deps)
        return await self._agent.turn(incoming(text), context_id, deps)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _picked_target(phase: Mapping[str, Any], text: str, deps: TurnDeps) -> walk.Outcome:
    if walk.interpret_answer(text)[0] == "skip":
        return walk.Outcome("complete", NO_TARGET_PICKED)
    options = {str(k): str(v) for k, v in (phase.get("options") or {}).items()}
    target = picker.read_target_answer(text, options)
    body = str(phase.get("body") or "")
    if target is None or not body:
        return walk.Outcome("complete", NO_TARGET_PICKED)
    planned = await graph.plan_post(deps, target, body, phase.get("facts") or {})
    return graph.outcome_of(planned)


async def _apply_outcome(updater: TaskUpdater, outcome: walk.Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.kind == "complete":
        artifact = [*parts, new_data_part(outcome.data)] if outcome.data else parts
        await updater.add_artifact(artifact, name="spend_spike")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    # The pause keys go on the status message: the platform builds the question from `status.message.metadata`
    # and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
