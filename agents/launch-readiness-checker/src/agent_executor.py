"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; the answer to "Which launch should I check?" resumes
from the paused task's metadata (the stored task is never re-enqueued). The turn is bounded by
TURN_DEADLINE_SECS and its LLM calls by MAX_LLM_CALLS. Nothing here writes anywhere."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

import question
import replies
from budget import MeteredLLM
from config import MAX_LLM_CALLS, TURN_DEADLINE_SECS
from deps import TurnDeps
from graph import LaunchAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

ARTIFACT_NAME = "launch_readiness"
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The result lives
# in the checkpointer, so only the current message is used (earlier replies stay out of prompts).
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


def json_requested(context: RequestContext) -> bool:
    """Metadata `{"output": "json"}` on the message (a workflow step) switches the text part to the envelope."""
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


@dataclass(frozen=True)
class Outcome:
    kind: str                     # "complete" | "input_required"
    text: str
    metadata: dict | None = None  # task metadata: the pause, to resume from
    hitl: dict | None = None      # status-message metadata: the question
    data: dict | None = None      # the envelope, sent as a data part


class LaunchExecutor(AgentExecutor):
    def __init__(self, agent: LaunchAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic,
                 deadline_secs: float = TURN_DEADLINE_SECS, max_llm_calls: int = MAX_LLM_CALLS):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now
        self._clock = clock
        self._deadline = deadline_secs
        self._max_llm_calls = max_llm_calls

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        deps = TurnDeps(llm=MeteredLLM(self._llm, self._max_llm_calls), model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), now=self._now(), started=self._clock(),
                        clock=self._clock)
        text = current_message(user_text(context))
        tracer = trace.get_tracer("launch-readiness-checker")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("launch_readiness.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                run = self._run(stored, text, task.context_id, deps, json_requested(context))
                outcome = await asyncio.wait_for(run, self._deadline)
            except TimeoutError:
                logger.warning("turn passed its %ss deadline", self._deadline)
                outcome = Outcome("complete", replies.TIMED_OUT)
            except Exception as e:
                logger.exception("launch-readiness-checker turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(replies.FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps, json_mode: bool) -> Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            pause = MessageToDict(stored.metadata).get(question.METADATA_KEY)
            if not isinstance(pause, Mapping):
                return Outcome("complete", replies.NOT_WAITING)
            return _outcome_of(await self._agent.resume(pause, text, context_id, deps))
        return _outcome_of(await self._agent.turn(text, context_id, deps, json_mode))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _outcome_of(result: TurnResult) -> Outcome:
    if result.pause is not None:
        text = "\n\n".join(p for p in (result.reply, result.pause.text) if p)
        return Outcome("input_required", text, result.pause.metadata, result.pause.hitl)
    return Outcome("complete", result.reply or replies.UNREADABLE_ANSWER, data=result.envelope)


async def _apply_outcome(updater: TaskUpdater, outcome: Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.kind == "complete":
        extra = [new_data_part(outcome.data)] if outcome.data is not None else []
        await updater.add_artifact([*parts, *extra], name=ARTIFACT_NAME)
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
        return
    # The question goes on the status message: the platform builds the card from `status.message.metadata`.
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
