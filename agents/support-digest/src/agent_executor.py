"""A2A executor (a2a-sdk 1.x): every turn runs the graph and completes; this agent never pauses. A digest
reply carries two parts: the markdown and a DataPart with the envelope (in JSON mode the text part is the
envelope as a fenced json block, because a workflow step reads only text)."""

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

import render
from budget import BudgetedLLM
from deps import TurnDeps
from graph import DigestAgent
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from request import wants_json
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The digest
# lives in the checkpointer, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "Current message: "


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are instructions, never the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def current_message(raw: str) -> str:
    idx = raw.rfind(_CURRENT_MESSAGE_MARKER)
    if idx == -1 or (idx > 0 and raw[idx - 1] != "\n"):
        return raw
    return raw[idx + len(_CURRENT_MESSAGE_MARKER):]


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


class DigestExecutor(AgentExecutor):
    def __init__(self, agent: DigestAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        text = current_message(user_text(context))
        tracer = trace.get_tracer("support-digest")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("support_digest.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(text, task.context_id, self._deps(context))
            except Exception as e:
                logger.exception("support digest turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
        body = render.as_json(result.envelope) if result.envelope and json_requested(context, text) else result.reply
        parts = [new_text_part(body)]
        artifact = [*parts, new_data_part(result.envelope)] if result.envelope else parts
        await updater.add_artifact(artifact, name="support_digest")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()
