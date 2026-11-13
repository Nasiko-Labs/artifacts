"""A2A executor (a2a-sdk 1.x), stateless: every message runs the graph once and ends the task `completed`
(or `failed` when a platform call fails). Nothing pauses and nothing is kept between messages; earlier turns
come only from the history block the platform prepends to the message.

A finished reply's artifact carries two parts: the markdown (or fenced JSON when asked) for people and MAF
steps, and a data part with the full envelope for API clients."""

from __future__ import annotations

import logging
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

from graph import ReplyDrafterAgent, TurnResult
from inputs import Request, split_history
from llm import TurnDeps
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."


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


def org_parts(context: RequestContext) -> tuple[str, ...]:
    return tuple(t for t in message_texts(context)[1:] if t.strip())


def metadata_wants_json(context: RequestContext) -> bool:
    message = context.message
    if message is None or not message.HasField("metadata"):
        return False
    return str(MessageToDict(message.metadata).get("output") or "").casefold() == "json"


def request_of(context: RequestContext) -> Request:
    earlier, text = split_history(user_text(context))
    return Request(text=text, org=org_parts(context), wants_json=metadata_wants_json(context), history=earlier)


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


class ReplyDrafterExecutor(AgentExecutor):
    def __init__(self, agent: ReplyDrafterAgent, llm, model: str, today: Callable[[], date] = utc_today):
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
        tracer = trace.get_tracer("reply-drafter")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("reply_drafter.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._agent.turn(request_of(context), deps)
            except Exception as e:
                logger.exception("reply-drafter turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    text = [new_text_part(result.reply)]
    parts = [*text, new_data_part(result.data)] if result.data else text
    await updater.add_artifact(parts, name="reply")
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
