"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to the "Which team?" pause resumes
the original request with that team, read from the paused task's own metadata (the durable task store).
A completed reply with a report carries two parts: the markdown (or the fenced JSON envelope when JSON
was asked for) and a DataPart with the envelope."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
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

import pause
import prompts
from deps import TurnDeps
from graph import ScopeAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from request import parse
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
ARTIFACT_NAME = "scope_change"
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The report
# lives in the checkpointer, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "Current message: "


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are never part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def current_message(raw: str) -> str:
    idx = raw.rfind(_CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(_CURRENT_MESSAGE_MARKER):]


def json_requested(context: RequestContext, text: str) -> bool:
    message = context.message
    metadata = MessageToDict(message.metadata) if message is not None and message.HasField("metadata") else {}
    return str(metadata.get("output") or "").casefold() == "json" or parse(text).as_json


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


class ScopeCreepExecutor(AgentExecutor):
    def __init__(self, agent: ScopeAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        text = current_message(user_text(context)).strip()
        tracer = trace.get_tracer("scope-creep-detector")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("scope_creep.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._run(stored, text, task.context_id, deps, json_requested(context, text))
            except Exception as e:
                logger.exception("scope creep turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, result)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps, as_json: bool) -> TurnResult:
        waiting = _paused_question(stored)
        if waiting is None:
            return await self._agent.turn(text, context_id, deps, as_json)
        teams = [str(t) for t in waiting.get("teams") or ()]
        team = pause.match_team(text, teams)
        if team is None:
            return TurnResult(prompts.TEAM_UNMATCHED.format(answer=text, teams=", ".join(teams)))
        query = str(waiting.get("query") or "")
        return await self._agent.turn(query, context_id, deps, as_json or parse(query).as_json, team_override=team)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _paused_question(stored) -> Mapping | None:
    if stored is None or stored.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
        return None
    # The paused task's metadata round-trips as a protobuf Struct.
    waiting = MessageToDict(stored.metadata).get(pause.PAUSE_KEY)
    return waiting if isinstance(waiting, Mapping) else None


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    if result.pause:
        # The question block goes on the status message: the platform builds the card from
        # `status.message.metadata`; the task metadata carries what the resume needs.
        parts = [new_text_part(result.pause["text"])]
        await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                    message=updater.new_agent_message(parts, metadata=result.pause["hitl"]),
                                    metadata=result.pause["metadata"])
        return
    parts = [new_text_part(result.reply)]
    artifact = [*parts, new_data_part(result.envelope)] if result.envelope else parts
    await updater.add_artifact(artifact, name=ARTIFACT_NAME)
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
