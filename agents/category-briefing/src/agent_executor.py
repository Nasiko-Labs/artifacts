"""A2A executor: one stateless request → one market pulse."""

from __future__ import annotations

import logging
from collections.abc import Callable

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

from crew import LlmSettings
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from pulse import market_pulse
from telemetry import inbound_traceparent, request_otel_context

log = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't produce the briefing: {reason}. Try again."


class BriefingExecutor(AgentExecutor):
    def __init__(
        self,
        gateway_factory: Callable[[], NasikoMcpClient | None] = NasikoMcpClient.from_env,
        settings_factory: Callable[[], LlmSettings] = LlmSettings.from_env,
    ):
        self._gateway_factory = gateway_factory
        self._settings_factory = settings_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        # Per request, never cached: the platform authorizes each call by this header.
        traceparent = extract_traceparent(context) or inbound_traceparent.get()

        tracer = trace.get_tracer("category-briefing")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("category_briefing.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            await self._status(event_queue, task, TaskState.TASK_STATE_WORKING, "Reading this week's market...")
            gateway = None
            try:
                gateway = self._gateway_factory()
                result = await market_pulse(context.get_user_input(), traceparent, gateway, self._settings_factory())
            except Exception as e:  # the A2A caller gets a clear failure, the log gets the detail
                log.exception("market pulse failed")
                span.record_exception(e)
                await self._status(event_queue, task, TaskState.TASK_STATE_FAILED,
                                   FAILED_TEXT.format(reason=type(e).__name__))
                return
            finally:
                await _close(gateway)

            await event_queue.enqueue_event(new_text_artifact_update_event(
                task_id=task.id, context_id=task.context_id, name="market_pulse", text=result.text,
            ))
            state = TaskState.TASK_STATE_COMPLETED if result.completed else TaskState.TASK_STATE_FAILED
            await self._status(event_queue, task, state, result.text)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Cancelling the running execute() stops the crew's tool calls (pulse sets its stop flag).
        task = context.current_task
        if task is not None:
            await self._status(event_queue, task, TaskState.TASK_STATE_CANCELED, "Cancelled.")

    @staticmethod
    async def _status(event_queue: EventQueue, task, state: TaskState, text: str) -> None:
        await event_queue.enqueue_event(new_text_status_update_event(
            task_id=task.id, context_id=task.context_id, state=state, text=text,
        ))


async def _close(gateway: NasikoMcpClient | None) -> None:
    if gateway is None:
        return
    try:
        await gateway.aclose()
    except Exception:  # closing must not mask the request's own outcome
        log.warning("closing the gateway client failed", exc_info=True)
