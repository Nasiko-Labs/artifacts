"""A2A executor (a2a-sdk 1.x): every turn runs the support agent and completes the task. A clarifying
question is an ordinary reply; its state is kept per contextId by the agent (followup.py)."""

from __future__ import annotations

import logging
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

from agent import SupportAgent
from llm import TurnDeps
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: something went wrong on my side. Please try again."


def utc_today() -> date:
    return datetime.now(UTC).date()


def message_texts(context: RequestContext) -> list[str]:
    message = context.message
    return list(get_text_parts(message.parts)) if message is not None else []


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are instructions, never the request."""
    texts = message_texts(context)
    return texts[0] if texts else ""


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


class SupportExecutor(AgentExecutor):
    def __init__(self, agent: SupportAgent, llm, model: str, today: Callable[[], date] = utc_today):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._today = today

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, traceparent=inbound_traceparent(context),
                        today=self._today())
        tracer = trace.get_tracer("nasiko-support-agent")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("support.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(user_text(context), task.context_id, deps)
            except Exception as e:
                logger.exception("support turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            span.set_attribute("support.mode", result.envelope.get("mode", ""))
        parts = [new_text_part(result.reply)]
        await updater.add_artifact(parts, name="answer")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
