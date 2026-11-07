"""A2A executor (a2a-sdk 1.x): every turn runs the graph and completes. There are no pauses: the
agent is read-only, so nothing needs an approval."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

from a2a.helpers import get_text_parts, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from deps import TurnDeps
from graph import PulseAgent
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The cards
# live in the checkpointer, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


def utc_today() -> date:
    return datetime.now(UTC).date()


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are never part of the request."""
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


class TeamPulseExecutor(AgentExecutor):
    def __init__(self, agent: PulseAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 today: Callable[[], date] = utc_today, clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._today = today
        self._clock = clock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), today=self._today(), clock=self._clock,
                        started=self._clock())
        text = current_message(user_text(context))
        tracer = trace.get_tracer("team-pulse")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("team_pulse.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                reply = await self._agent.turn(text, task.context_id, deps)
            except Exception as e:
                logger.exception("team pulse turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
        parts = [new_text_part(reply)]
        await updater.add_artifact(parts, name="team_pulse")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
