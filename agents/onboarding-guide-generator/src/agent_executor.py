"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the
repository question, from the task's own metadata. The stored task is never re-enqueued. A reply with a
guide carries two parts: the markdown and a DataPart with the envelope (in JSON mode the text part is
the envelope as a fenced json block)."""

from __future__ import annotations

import json
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

from deps import TurnDeps
from graph import OnboardingAgent, TurnResult
from intake import wants_json
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
PAUSE_KEY = "onboarding_pause"
ARTIFACT_NAME = "onboarding_guide"
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The guide
# lives in the checkpointer, so only the current message is used (earlier replies stay out of prompts).
_CURRENT_MESSAGE_MARKER = "Current message: "


@dataclass(frozen=True)
class Outcome:
    kind: str  # complete | input_required
    text: str
    metadata: dict | None = None
    hitl: dict | None = None
    envelope: dict | None = None


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


class OnboardingExecutor(AgentExecutor):
    def __init__(self, agent: OnboardingAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        text = current_message(user_text(context)).strip()
        as_json = json_requested(context, text)
        tracer = trace.get_tracer("onboarding-guide-generator")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("onboarding.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, text, task.context_id, deps, as_json)
            except Exception as e:
                logger.exception("onboarding turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps, as_json: bool) -> Outcome:
        pause = None
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            pause = MessageToDict(stored.metadata).get(PAUSE_KEY)
        if isinstance(pause, Mapping) and pause.get("kind") == "repo":
            result = await self._agent.resume_repo(text, str(pause.get("asked") or ""), context_id, deps)
        else:
            result = await self._agent.turn(text, context_id, deps)
        return _outcome_of(result, as_json)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _outcome_of(result: TurnResult, as_json: bool) -> Outcome:
    if as_json and result.envelope:  # a workflow step: the envelope as text, and never a question
        return Outcome("complete", "```json\n" + json.dumps(result.envelope, indent=1) + "\n```",
                       envelope=result.envelope)
    if result.pause is not None:
        metadata = {PAUSE_KEY: {"kind": result.pause.kind, "asked": result.pause.asked}}
        text = "\n\n".join(p for p in (result.reply, result.pause.text) if p)
        return Outcome("input_required", text, metadata, None, result.envelope)
    return Outcome("complete", result.reply, envelope=result.envelope)


async def _apply_outcome(updater: TaskUpdater, outcome: Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.kind == "complete":
        artifact = [*parts, new_data_part(outcome.envelope)] if outcome.envelope else parts
        await updater.add_artifact(artifact, name=ARTIFACT_NAME)
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
        return
    # The question goes on the status message: the platform builds the card from its metadata.
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
