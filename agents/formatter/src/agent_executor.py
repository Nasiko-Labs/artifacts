"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes from the
question kept in the task's own metadata. The stored task is never re-enqueued.

A finished rendering's artifact carries the text (`formatted` plus a status line, or the fenced
envelope when asked) and a data part with the envelope. MAF and the chat read the text only. There is
no model call, so the inbound traceparent authorises nothing here; it only parents the request span."""

from __future__ import annotations

import logging

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

import hitl
from graph import FormatterAgent, TurnResult
from inputs import Incoming, read_parts
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: something went wrong while formatting. Please try again."
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


def user_input(context: RequestContext) -> Incoming:
    """The user's own words are the FIRST text part only; later text parts are org policy; a JSON or
    text file part is data."""
    message = context.message
    if message is None:
        return Incoming(text="")
    metadata = MessageToDict(message.metadata) if message.HasField("metadata") else {}
    return read_parts(message.parts, metadata)


def user_text(context: RequestContext) -> str:
    message = context.message
    texts = list(get_text_parts(message.parts)) if message is not None else []
    return texts[0] if texts else ""


class FormatterExecutor(AgentExecutor):
    def __init__(self, agent: FormatterAgent):
        self._agent = agent

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        tracer = trace.get_tracer("formatter")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("formatter.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._run(stored, user_input(context), task.context_id)
            except Exception as e:
                logger.exception("formatter turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, result)

    async def _run(self, stored, incoming: Incoming, context_id: str) -> TurnResult:
        if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            # The paused task's metadata round-trips as a protobuf Struct.
            pending = hitl.Pending.from_metadata(MessageToDict(stored.metadata))
            if pending is None:  # fail closed: never guess what was asked
                return TurnResult(NOT_WAITING)
            return await self._agent.turn(incoming, context_id, pending=pending)
        return await self._agent.turn(incoming, context_id)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    if result.pause is None:
        text = [new_text_part(result.reply)]
        parts = [*text, new_data_part(result.envelope)] if result.envelope is not None else text
        await updater.add_artifact(parts, name="formatted")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
        return
    # The question goes on the status message (the platform builds the request card from
    # `status.message.metadata`); what to resume from goes on the task's metadata.
    text, question = hitl.question(result.pause)
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message([new_text_part(text)], metadata=question),
                                metadata=result.pause.to_metadata())
