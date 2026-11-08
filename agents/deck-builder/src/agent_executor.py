"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph (and, when asked, the upload offer); an answer to
a paused task resumes from the task's own metadata. Reply parts: the markdown text, the envelope DataPart
and the .pptx file part (dropped by the chat UI, kept for API clients). The stored task is never re-enqueued."""

from __future__ import annotations

import logging

from a2a.helpers import (
    get_text_parts,
    new_data_part,
    new_raw_part,
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
import resume as r
from deps import TurnDeps
from graph import DeckAgent
from inputs import current_message
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from pipeline import MAX_FILE_BYTES
from telemetry import request_otel_context
from uploader import PPTX_MIME
from walk import Outcome

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
TOO_BIG = "The file is over 5 MB, so it isn't attached; use the script or the upload instead."
ARTIFACT_NAME = "deck"
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
_PAUSE_KINDS = {"input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
                "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED}


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. Later text parts are org policy and agent knowledge."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def inbound_traceparent(context: RequestContext) -> str | None:
    """This request's traceparent: the header a2a-sdk recorded, else the one TraceparentMiddleware extracted."""
    header = extract_traceparent(context)
    if header:
        return header
    extracted = request_otel_context.get()
    if extracted is None or not trace.get_current_span(extracted).get_span_context().is_valid:
        return None
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier, context=extracted)
    return carrier.get("traceparent")


class DeckBuilderExecutor(AgentExecutor):
    def __init__(self, agent: DeckAgent, llm, model: str, gateway: NasikoMcpClient | None):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context))
        text = current_message(user_text(context))
        tracer = trace.get_tracer("deck-builder")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("deck_builder.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome, as_json = await self._run(stored, text, task.context_id, deps)
            except Exception as e:
                logger.exception("deck builder turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, outcome, as_json)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps) -> tuple[Outcome, bool]:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            return await r.resume(deps, self._agent, context_id, MessageToDict(stored.metadata), text), False
        result = await self._agent.turn(text, context_id, deps)
        return await r.from_turn(deps, result), result.as_json

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


def envelope_of(outcome: Outcome) -> dict | None:
    built = outcome.built
    if built is None:
        return None
    stored = built.stored
    return render.envelope(stored.deck, stored.report, name=stored.file_name, size=len(built.data), sha=built.sha,
                           chart_failures=stored.chart_failures, uploaded=outcome.uploaded,
                           upload_unavailable=outcome.upload_unavailable)


async def _apply(updater: TaskUpdater, outcome: Outcome, as_json: bool) -> None:
    envelope = envelope_of(outcome)
    text = outcome.text
    if as_json and envelope is not None:  # a workflow step: the envelope as text
        text = render.json_block(envelope)
    if outcome.kind == "complete":
        parts = [new_text_part(text)]
        extra = [new_data_part(envelope)] if envelope else []
        if outcome.built is not None:
            data = outcome.built.data
            if len(data) <= MAX_FILE_BYTES:
                extra.append(new_raw_part(data, media_type=PPTX_MIME, filename=outcome.built.stored.file_name))
            else:
                parts = [new_text_part(f"{text}\n\n{TOO_BIG}")]
        await updater.add_artifact([*parts, *extra], name=ARTIFACT_NAME)
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    # The pause keys go on the status message: the platform builds the question from `status.message.metadata`
    # and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message([new_text_part(text)], metadata=outcome.hitl),
                                metadata=outcome.metadata)

