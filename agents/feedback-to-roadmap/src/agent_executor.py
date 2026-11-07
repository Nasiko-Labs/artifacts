"""A2A executor (a2a-sdk 1.x): every turn runs the graph (read-only, no pauses). A finished result's artifact
carries two parts: the markdown (or fenced JSON when asked) for people and workflow steps, and a data part with
the full envelope for API clients."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
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

from budget import BudgetedLLM
from deps import TurnDeps
from graph import RoadmapAgent
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent knowledge as
    later, unmarked text parts; those are instructions, never the request or data."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def current_message(raw: str) -> str:
    idx = raw.rfind(_CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(_CURRENT_MESSAGE_MARKER):]


def wants_json(context: RequestContext) -> bool:
    message = context.message
    if message is None:
        return False
    meta = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    return str(meta.get("output", "")).casefold() == "json"


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


class RoadmapExecutor(AgentExecutor):
    def __init__(self, agent: RoadmapAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        text = current_message(user_text(context))
        tracer = trace.get_tracer("feedback-to-roadmap")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("feedback_to_roadmap.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(text, task.context_id, deps, wants_json(context))
            except Exception as e:
                logger.exception("feedback-to-roadmap turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
        text_part = [new_text_part(result.reply)]
        parts = [*text_part, new_data_part(result.data)] if result.data else text_part
        await updater.add_artifact(parts, name="feedback-to-roadmap")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text_part))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
