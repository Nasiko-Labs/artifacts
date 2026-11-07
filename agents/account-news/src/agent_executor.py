"""A2A executor (a2a-sdk 1.x event-queue API): one graph turn per message, keyed by contextId."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timezone

from a2a.helpers import (
    new_task_from_user_message,
    new_text_artifact_update_event,
    new_text_status_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from graph import AccountNewsAgent, TurnDeps
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


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


class AccountNewsExecutor(AgentExecutor):
    def __init__(self, agent: AccountNewsAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 today: Callable[[], date] = utc_today):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._today = today

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            # Only a new task is enqueued whole (see the langgraph template for why).
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), today=self._today())
        tracer = trace.get_tracer("account-news")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("account_news.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            await self._status(event_queue, task, TaskState.TASK_STATE_WORKING, "Looking for recent news...")
            try:
                reply = await self._agent.answer(context.get_user_input(), task.context_id, deps)
            except Exception as e:
                logger.exception("account news turn failed")
                span.record_exception(e)
                await self._status(event_queue, task, TaskState.TASK_STATE_FAILED, FAILED_TEXT)
                return
            await event_queue.enqueue_event(new_text_artifact_update_event(
                task_id=task.id, context_id=task.context_id, name="account_news", text=reply,
            ))
            await self._status(event_queue, task, TaskState.TASK_STATE_COMPLETED, reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass

    @staticmethod
    async def _status(event_queue: EventQueue, task, state, text: str) -> None:
        await event_queue.enqueue_event(new_text_status_update_event(
            task_id=task.id, context_id=task.context_id, state=state, text=text,
        ))
