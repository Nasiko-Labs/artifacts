"""A2A executor (a2a-sdk 1.x). Routes each message in code:

    greeting / small talk           → the intro (no tool or LLM call)
    carries a renewal input label   → the 1.1.0 renewal brief, unchanged (1 call + at most 1 repair)
    anything else                   → meeting-card mode (prep.py), read-only
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime

from a2a.helpers import (
    get_text_parts,
    new_task_from_user_message,
    new_text_artifact_update_event,
    new_text_part,
    new_text_status_update_event,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from opentelemetry import context as otel_context
from opentelemetry import trace

import prep
import smalltalk
from cache import MemoryCache, PrepCache
from deps import TurnDeps
from llm import generate_brief, traceparent_headers
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from prompts import INPUT_LABELS
from telemetry import inbound_traceparent as inbound_header
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE_ENV = "DEFAULT_TIMEZONE"
FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. Earlier turns
# live in the card cache, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
_RENEWAL_LABEL = re.compile("|".join(rf"\b{re.escape(label)}\s*:" for label in INPUT_LABELS), re.IGNORECASE)


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


def is_renewal_request(text: str) -> bool:
    """The renewal-brief path: the message carries one of the three pasted-input labels."""
    return bool(_RENEWAL_LABEL.search(text or ""))


def request_traceparent(context: RequestContext) -> str | None:
    """This request's traceparent: the header a2a-sdk recorded, else the one TraceparentMiddleware saw."""
    return extract_traceparent(context) or inbound_header.get()


class PrepExecutor(AgentExecutor):
    def __init__(self, llm, model: str, gateway: NasikoMcpClient | None = None, cache: PrepCache | None = None,
                 now: Callable[[], datetime] = utc_now, default_tz: str | None = None,
                 monotonic: Callable[[], float] | None = None):
        self.llm = llm
        self.model = model
        self._gateway = gateway
        self._cache = cache or MemoryCache()
        self._now = now
        self._default_tz = default_tz if default_tz is not None else os.environ.get(DEFAULT_TIMEZONE_ENV)
        self._monotonic = monotonic

    async def _status(self, event_queue: EventQueue, task, state, text: str) -> None:
        await event_queue.enqueue_event(
            new_text_status_update_event(
                task_id=task.id, context_id=task.context_id, state=state, text=text,
            )
        )

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        text = current_message(user_text(context))

        tracer = trace.get_tracer("prep-agent")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("prep.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            canned = smalltalk.reply_to(text)
            if canned is not None:  # a greeting: no tool call, no LLM call
                await _complete(TaskUpdater(event_queue, task.id, task.context_id), canned)
            elif is_renewal_request(text):
                await self._renewal(event_queue, task, text, span)
            else:
                await self._cards(TaskUpdater(event_queue, task.id, task.context_id), context, task.context_id,
                                  text, span)

    async def _renewal(self, event_queue: EventQueue, task, query: str, span) -> None:
        """The 1.1.0 renewal brief, as shipped: same prompt, call, repair, events and messages."""
        await self._status(event_queue, task, TaskState.TASK_STATE_WORKING, "Preparing brief...")
        try:
            result = await generate_brief(self.llm, self.model, query, traceparent_headers())
        except Exception as e:
            logger.exception("brief generation failed")
            span.record_exception(e)
            await self._status(
                event_queue, task, TaskState.TASK_STATE_FAILED,
                "Could not write the brief: the model call failed. Try again.",
            )
            return
        await event_queue.enqueue_event(
            new_text_artifact_update_event(
                task_id=task.id, context_id=task.context_id,
                name="response", text=result or "No response",
            )
        )
        await self._status(event_queue, task, TaskState.TASK_STATE_COMPLETED, "Brief ready")

    async def _cards(self, updater: TaskUpdater, context: RequestContext, context_id: str, text: str, span) -> None:
        await updater.start_work()
        clock = {"monotonic": self._monotonic} if self._monotonic is not None else {}
        deps = TurnDeps(llm=self.llm, model=self.model, gateway=self._gateway,
                        traceparent=request_traceparent(context), now=self._now(), default_tz=self._default_tz,
                        **clock)
        try:
            reply = await prep.handle(deps, text, context_id, self._cache)
        except Exception as e:
            logger.exception("meeting prep turn failed")
            span.record_exception(e)
            await updater.update_status(TaskState.TASK_STATE_FAILED,
                                        message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
            return
        await _complete(updater, reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _complete(updater: TaskUpdater, text: str) -> None:
    parts = [new_text_part(text)]
    await updater.add_artifact(parts, name="meeting_prep")
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
