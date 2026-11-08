"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes from the
job kept in the task's own metadata. The stored task is never re-enqueued.

A finished conversion's artifact carries two parts: the Markdown rendering (or the fenced JSON envelope
when asked) for people and workflow steps, and a data part with the full envelope for API clients."""

from __future__ import annotations

import asyncio
import logging
import time

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
import resume
from convert import Converter
from deps import TURN_DEADLINE_SECS, TurnDeps
from graph import ConverterAgent, TurnResult
from hitl import Pause
from inputs import current_message, read_message
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from netguard import SafeFetcher
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
TIMED_OUT_TEXT = "This took longer than this agent's time limit for one turn, so I stopped. Try fewer or smaller files."


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


class DocConverterExecutor(AgentExecutor):
    def __init__(self, agent: ConverterAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 converter: Converter | None = None, fetcher: SafeFetcher | None = None,
                 deadline_secs: float = TURN_DEADLINE_SECS):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._converter = converter
        self._fetcher = fetcher
        self._deadline_secs = deadline_secs

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), converter=self._converter, fetcher=self._fetcher,
                        deadline=time.monotonic() + self._deadline_secs)
        tracer = trace.get_tracer("doc-converter")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("doc_converter.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                async with asyncio.timeout(self._deadline_secs):
                    result = await self._run(stored, context, task.context_id, deps)
            except TimeoutError:
                result = TurnResult(TIMED_OUT_TEXT)
            except Exception as e:
                logger.exception("doc-converter turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, result)

    async def _run(self, stored, context: RequestContext, context_id: str, deps: TurnDeps) -> TurnResult:
        if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            # The paused task's metadata round-trips as a protobuf Struct.
            pause = Pause.from_metadata(MessageToDict(stored.metadata))
            if pause is None:  # fail closed: never guess what was asked
                return TurnResult(prompts.NOT_WAITING)
            scope, step = resume.next_step(pause, current_message(user_text(context)), deps)
            if scope is None:
                return TurnResult(step)
            return await self._agent.resume(scope, step, context_id)
        return await self._agent.turn(read_message(context.message), context_id, deps)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    if result.pause is None:
        text = [new_text_part(result.reply)]
        parts = [*text, new_data_part(result.data)] if result.data else text
        await updater.add_artifact(parts, name="doc_converter")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
        return
    # The question goes on the status message (the platform builds the request card from
    # `status.message.metadata`); what to resume from goes on the task's metadata.
    text, question = result.pause.question()
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message([new_text_part(text)], metadata=question),
                                metadata=result.pause.to_metadata())
