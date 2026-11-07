"""The as-built research executor; the MCP layer is the shared kit (bearer + traceparent)."""

import logging

from a2a.helpers import (
    new_task_from_user_message,
    new_text_artifact_update_event,
    new_text_status_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types import TaskState
from opentelemetry import context as otel_context, trace

from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from research import run_research
from telemetry import inbound_traceparent, request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "The GitHub lookup failed at the platform, so I will not guess. Try again."


class ResearchExecutor(AgentExecutor):
    def __init__(self, llm, model: str, gateway: NasikoMcpClient | None):
        self.llm = llm
        self.model = model
        self.gateway = gateway

    async def _status(self, event_queue: EventQueue, task, state, text: str) -> None:
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=task.id, context_id=task.context_id, state=state, text=text,
            )
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)

        tracer = trace.get_tracer("research-agent")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("research.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                await self._status(event_queue, task, TaskState.TASK_STATE_WORKING, "Researching...")
                # Per request, never cached: the platform authorizes MCP and LLM calls by this flow.
                traceparent = extract_traceparent(context) or inbound_traceparent.get()
                result = await run_research(
                    self.llm, self.model, context.get_user_input(), self.gateway, traceparent
                )
            except Exception as e:
                logger.exception("research failed")
                span.record_exception(e)
                await self._status(event_queue, task, TaskState.TASK_STATE_FAILED, FAILED_TEXT)
                return

            await event_queue.enqueue_event(
                new_text_artifact_update_event(
                    task_id=task.id, context_id=task.context_id, name="response", text=result,
                )
            )
            await self._status(event_queue, task, TaskState.TASK_STATE_COMPLETED, result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass
