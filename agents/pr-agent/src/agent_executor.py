"""A2A executor (a2a-sdk 1.x event-queue API): one graph turn per message, keyed by contextId.

A new message runs the graph. A message on a task paused in input-required (the post
confirmation, or the gateway's approval card) resumes the paused graph with the answer.
A pause becomes `input-required` whose `status.message.metadata` carries the structured
question (Post / Don't post) or the gateway's `hitl_request_id`.
"""

from __future__ import annotations

import logging
from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from commands import current_message
from diff import DEFAULT_DIFF_TOKEN_BUDGET
from graph import PrAgent, TurnDeps, TurnOutcome
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
LOST_STATE = ("I no longer have the paused request (the agent restarted without durable state). "
              "Please ask again.")
WORKING_TEXT = "Reading the pull request..."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)


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


class PrAgentExecutor(AgentExecutor):
    def __init__(self, agent: PrAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 diff_budget: int = DEFAULT_DIFF_TOKEN_BUDGET):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._diff_budget = diff_budget

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:
            # Only a new task is enqueued whole (see the langgraph template for why).
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        is_resume = stored is not None and stored.status.state in _PAUSED_STATES
        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), diff_budget=self._diff_budget)
        tracer = trace.get_tracer("pr-agent")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("pr_agent.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            await updater.update_status(TaskState.TASK_STATE_WORKING,
                                        message=updater.new_agent_message([new_text_part(WORKING_TEXT)]))
            try:
                outcome = await self._turn(context, task.context_id, deps, is_resume)
            except Exception as e:
                logger.exception("pr-agent turn failed")
                span.record_exception(e)
                await updater.failed(updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply(updater, outcome)

    async def _turn(self, context: RequestContext, context_id: str, deps: TurnDeps, is_resume: bool) -> TurnOutcome:
        text = context.get_user_input()
        if not is_resume:
            return await self._agent.answer(text, context_id, deps)
        resumed = await self._agent.resume(current_message(text).strip(), context_id, deps)
        return resumed or TurnOutcome(reply=LOST_STATE)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, outcome: TurnOutcome) -> None:
    if outcome.pause is not None:
        message = updater.new_agent_message([new_text_part(outcome.pause["text"])],
                                            metadata=outcome.pause.get("metadata"))
        await updater.update_status(TaskState.TASK_STATE_INPUT_REQUIRED, message=message)
        return
    await updater.add_artifact([new_text_part(outcome.reply)], name="pr_agent")
    await updater.complete(message=updater.new_agent_message([new_text_part(outcome.reply)]))
