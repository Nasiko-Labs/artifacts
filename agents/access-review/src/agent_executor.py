"""A2A executor (a2a-sdk 1.x): every message runs the graph; an answer to the paused organization question
reaches it on the same task and the graph's pending state picks it up. The stored task is never
re-enqueued. A review reply carries two parts: the markdown and a DataPart with the envelope (in JSON
mode the text part is the envelope as a fenced json block, and no question is ever asked)."""

from __future__ import annotations

import json
import logging
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

import prompts
from deps import TurnDeps
from graph import AccessReviewAgent, TurnResult
from intake import wants_json
from nasiko_hitl import question_metadata
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
ARTIFACT_NAME = "access_review"
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The review
# lives in the checkpointer, so only the current message is used (earlier replies stay out of prompts).
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


class AccessReviewExecutor(AgentExecutor):
    def __init__(self, agent: AccessReviewAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 now: Callable[[], datetime] = utc_now):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), now=self._now())
        text = current_message(user_text(context))
        as_json = json_requested(context, text)
        tracer = trace.get_tracer("access-review")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("access_review.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(text, task.context_id, deps, as_json)
            except Exception as e:
                logger.exception("access review turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, result, as_json)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult, as_json: bool) -> None:
    if result.ask and not as_json:
        hitl = question_metadata(result.ask_header or prompts.ASK_ORG_HEADER, [prompts.SKIP_ORG])
        parts = [new_text_part(result.reply)]
        await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                    message=updater.new_agent_message(parts, metadata=hitl))
        return
    envelope = result.envelope
    text = result.reply
    if as_json and envelope:
        text = "```json\n" + json.dumps(envelope, indent=1) + "\n```"
    parts = [new_text_part(text)]
    await updater.add_artifact([*parts, new_data_part(envelope)] if envelope else parts, name=ARTIFACT_NAME)
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
