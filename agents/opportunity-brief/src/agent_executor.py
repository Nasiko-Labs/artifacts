"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the topic
question from the task's own metadata. The stored task is never re-enqueued.

A finished brief's artifact carries two parts: the markdown (or a fenced JSON envelope when asked) for people
and workflow steps, and a data part with the full envelope for API clients."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

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

import hitl
import prompts
import render
from deps import TURN_DEADLINE_SECS, TurnDeps
from graph import BriefAgent, TurnResult
from inputs import current_message, read_message
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; joining them would make them part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
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


class BriefExecutor(AgentExecutor):
    def __init__(self, agent: BriefAgent, llm: Any, model: str, gateway: NasikoMcpClient | None, hn_http: Any,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._hn_http = hn_http
        self._now = now
        self._clock = clock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway, hn_http=self._hn_http,
                        traceparent=inbound_traceparent(context), now=self._now(), clock=self._clock,
                        deadline=self._clock() + TURN_DEADLINE_SECS)
        incoming = read_message(context.message)
        tracer = trace.get_tracer("opportunity-brief")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("opportunity_brief.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result, wants_json = await self._run(stored, incoming, context, task.context_id, deps)
            except Exception as e:
                logger.exception("opportunity-brief turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            span.set_attribute("opportunity_brief.llm_calls", deps.budget.llm)
            span.set_attribute("opportunity_brief.hn_requests", deps.budget.hn)
            await _apply(updater, result, wants_json)

    async def _run(self, stored, incoming, context: RequestContext, context_id: str,
                   deps: TurnDeps) -> tuple[TurnResult, bool]:
        if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            pending = hitl.Pending.from_metadata(MessageToDict(stored.metadata))
            if pending is None:  # fail closed: never guess what was asked
                return TurnResult(NOT_WAITING), False
            action, value = hitl.interpret(pending, current_message(user_text(context)))
            if action == "cancel":
                return TurnResult(prompts.CANCELLED), False
            if action == "ask_which":
                return TurnResult("", pause=hitl.Pending(hitl.WHICH, pending.request, value,
                                                         pending.wants_json)), False
            return await self._agent.resume(pending, value, context_id, deps), pending.wants_json
        result = await self._agent.turn(incoming.text, incoming.wants_json, context_id, deps)
        return result, incoming.wants_json

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult, wants_json: bool) -> None:
    if result.pause is None:
        body = render.as_json(result.envelope) if wants_json and result.envelope else result.reply
        text = [new_text_part(body)]
        parts = [*text, new_data_part(result.envelope)] if result.envelope else text
        await updater.add_artifact(parts, name="opportunity_brief")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
        return
    # The question goes on the status message (the platform builds the request card from
    # `status.message.metadata`); what to resume from goes on the task's metadata.
    text, question = hitl.question(result.pause)
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message([new_text_part(text)], metadata=question),
                                metadata=result.pause.to_metadata())
