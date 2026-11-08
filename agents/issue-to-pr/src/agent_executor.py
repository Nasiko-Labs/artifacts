"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes from the
question kept in the task's own metadata. The stored task is never re-enqueued.

A finished turn's artifact carries two parts: the markdown (or fenced JSON when asked) for people and MAF
steps, and a data part with the full envelope for API clients. Turn 1 and revisions get the long deadline;
a write turn (one approved write) gets the short one."""

from __future__ import annotations

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

import hitl
import prompts
from graph import IssueToPrAgent, TurnResult
from inputs import Incoming, read_message
from llm import TURN_DEADLINE_SECS, WRITE_TURN_DEADLINE_SECS, TurnDeps
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Nothing was pushed. Please try again."


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


def deadline_for(pending: hitl.Pending | None) -> float:
    """A write turn (an approval on a card after the first) is short; everything else is long."""
    if pending is not None and pending.kind == "approval" and pending.step > 0:
        return WRITE_TURN_DEADLINE_SECS
    return TURN_DEADLINE_SECS


class IssueToPrExecutor(AgentExecutor):
    def __init__(self, agent: IssueToPrAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 engine=None, workspace=None, runner=None):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._engine = engine
        self._workspace = workspace
        self._runner = runner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        pending = None
        if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            pending = hitl.Pending.from_metadata(MessageToDict(stored.metadata))
        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context),
                        deadline=time.monotonic() + deadline_for(pending), engine=self._engine,
                        workspace=self._workspace, runner=self._runner, context_id=task.context_id)
        incoming = read_message(context.message)
        tracer = trace.get_tracer("issue-to-pr")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("issue-to-pr.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                result = await self._run(stored, pending, incoming, task.context_id, deps)
            except Exception as e:
                logger.exception("issue-to-pr turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            span.set_attribute("issue_to_pr.llm_calls", deps.budget.calls)
            span.set_attribute("issue_to_pr.tool_calls", deps.budget.tool_calls)
            await _apply(updater, result)

    async def _run(self, stored, pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        if stored is not None and stored.status.state == TaskState.TASK_STATE_INPUT_REQUIRED:
            if pending is None:  # fail closed: never guess what was asked
                return TurnResult(prompts.NOT_WAITING)
            return await self._agent.resume(pending, incoming, context_id, deps)
        return await self._agent.turn(incoming, context_id, deps)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: TurnResult) -> None:
    if result.pause is None:
        text = [new_text_part(result.reply)]
        parts = [*text, new_data_part(result.data)] if result.data else text
        await updater.add_artifact(parts, name="issue-to-pr")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(text))
        return
    # The question goes on the status message (the platform builds the request card from
    # `status.message.metadata`); what to resume from goes on the task's metadata.
    text, question = hitl.question(result.pause)
    await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED,
                                message=updater.new_agent_message([new_text_part(text)], metadata=question),
                                metadata=result.pause.to_metadata())
