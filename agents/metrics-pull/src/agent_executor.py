"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task (sheet name, sheet
pick, column pick) resumes from the task's own metadata, so it works on any replica. The stored task is
never re-enqueued. A pull also carries the envelope as a DataPart. (Pattern copied from the hardened kit;
owned by this agent.)"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
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

from deps import TurnDeps
from flow import PAUSE_KEY, TurnResult
from graph import MetricsPullAgent
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NOTHING_TO_SAY = "Done."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. Prior state
# lives in the checkpointer, so only the current message is used.
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


class MetricsPullExecutor(AgentExecutor):
    def __init__(self, agent: MetricsPullAgent, llm, model: str, gateway: NasikoMcpClient | None,
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
        tracer = trace.get_tracer("metrics-pull")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("metrics_pull.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._run(stored, text, task.context_id, deps, _message_metadata(context))
            except Exception as e:
                logger.exception("metrics-pull turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, result)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps,
                   message_metadata: Mapping[str, Any]) -> TurnResult:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            pause = MessageToDict(stored.metadata).get(PAUSE_KEY)
            if isinstance(pause, Mapping):
                return await self._agent.resume(pause, text, context_id, deps)
        return await self._agent.turn(text, context_id, deps, message_metadata)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _message_metadata(context: RequestContext) -> dict:
    message = context.message
    if message is None or not message.HasField("metadata"):
        return {}
    return MessageToDict(message.metadata)


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    question = result.question
    if question is not None:
        # The pause keys go on the task (for the resume) and the structured question on the status
        # message: the platform builds the request card from `status.message.metadata`.
        text = "\n\n".join(p for p in (result.reply, question.get("text")) if p)
        await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                    message=updater.new_agent_message([new_text_part(text)],
                                                                      metadata=question.get("hitl")),
                                    metadata=question.get("metadata"))
        return
    parts = [new_text_part(result.reply or NOTHING_TO_SAY)]
    if result.envelope is not None:  # the table for API clients; MAF reads only the text part
        await updater.add_artifact([*parts, new_data_part(result.envelope)], name="metrics_pull")
    else:
        await updater.add_artifact(parts, name="metrics_pull")
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
