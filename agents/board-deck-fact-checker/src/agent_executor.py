"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer on a task paused at "Which sheet is
the source?" resumes it. A finished check also carries the envelope as a DataPart (MAF reads only the text
part, so "as json" puts the envelope in the text instead)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

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
from graph import DeckAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
PICK_KEY = "board_deck_pick"
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The report lives
# in the checkpointer, so only the current message is used.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; those are never part of the request."""
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


def message_metadata(context: RequestContext) -> dict:
    message = context.message
    if message is None or not message.HasField("metadata"):
        return {}
    return MessageToDict(message.metadata)


class DeckExecutor(AgentExecutor):
    def __init__(self, agent: DeckAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._clock = clock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), clock=self._clock, started=self._clock())
        text = current_message(user_text(context))
        tracer = trace.get_tracer("board-deck-fact-checker")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("board_deck.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                if _is_paused_pick(stored):
                    result = await self._agent.resume(text, task.context_id, deps)
                else:
                    result = await self._agent.turn(text, task.context_id, deps, message_metadata(context))
            except Exception as e:
                logger.exception("board deck turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _publish(updater, result)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def _is_paused_pick(stored) -> bool:
    if stored is None or stored.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
        return False
    return bool(MessageToDict(stored.metadata).get(PICK_KEY))


async def _publish(updater: TaskUpdater, result: TurnResult) -> None:
    parts = [new_text_part(result.reply)]
    if result.question is not None:
        # The structured question goes on the status message: the platform builds the card from it.
        await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                    message=updater.new_agent_message(parts, metadata=result.question["metadata"]),
                                    metadata={PICK_KEY: True})
        return
    artifact = [*parts, new_data_part(result.envelope)] if result.envelope is not None else parts
    await updater.add_artifact(artifact, name="board_deck_check")
    await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts))
