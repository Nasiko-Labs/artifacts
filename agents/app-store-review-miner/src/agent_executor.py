"""A2A executor (a2a-sdk 1.x): every turn runs the graph; nothing pauses (read-only agent). A finished
turn's artifact carries the markdown (or the fenced JSON envelope when asked) and, when an analysis ran, a
data part with the full envelope for API clients."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime

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

import inputs
import replies
from budget import Budget
from config import Config
from deps import TurnDeps
from graph import MinerAgent
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)


def utc_today() -> date:
    return datetime.now(UTC).date()


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; joining them would make them part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def message_metadata(context: RequestContext) -> dict:
    message = context.message
    return MessageToDict(message.metadata) if message is not None and message.HasField("metadata") else {}


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


class ReviewMinerExecutor(AgentExecutor):
    def __init__(self, agent: MinerAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 today: Callable[[], date] = utc_today, config: Config | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._today = today
        self._config = config or Config()
        self._clock = clock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), today=self._today(), config=self._config,
                        clock=self._clock, started=self._clock(), budget=Budget(self._config.max_llm_calls))
        text = inputs.current_message(user_text(context))
        json_out = inputs.wants_json(text, message_metadata(context))
        tracer = trace.get_tracer("app-store-review-miner")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("review_miner.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(text, task.context_id, deps, json_out)
            except Exception as e:
                logger.exception("app store review miner turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(replies.FAILED_TEXT)]))
                return
            span.set_attribute("review_miner.llm_calls", deps.budget.calls)
            text_part = [new_text_part(result.reply)]
            parts = [*text_part, new_data_part(result.data)] if result.data else text_part
            await updater.add_artifact(parts, name="app_review_analysis")
            await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text_part))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
