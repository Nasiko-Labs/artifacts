"""A2A executor (a2a-sdk 1.x): a fresh turn runs the graph; an answer to a paused task resumes the customer
or dispute pick, the "what do you want to tell the customer?" question, or the approve → send walk, from the
task's own metadata. The stored task is never re-enqueued. A case turn also carries the envelope as a
DataPart. Later text parts (org policy) are passed on as policy text, never as the request.
(Pattern copied from the hardened kit; owned by this agent.)"""

from __future__ import annotations

import logging
import time
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

import walk
from deps import TurnDeps
from drafts import interpret_answer
from graph import DECISION_KEY, PICK_KEY, CaseAgent, TurnResult
from nasiko_mcp import NasikoMcpClient
from nasiko_trace import extract_traceparent
from telemetry import request_otel_context

logger = logging.getLogger(__name__)

FAILED_TEXT = "I couldn't finish this request: a platform call failed. Please try again."
NO_PICK_TEXT = "OK. Tell me the customer's email address or the charge or dispute id whenever you're ready."
NOTHING_TEXT = "There is nothing to do for that request."
_PAUSED_STATES = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
_PAUSE_KINDS = {"input_required": TaskState.TASK_STATE_INPUT_REQUIRED,
                "auth_required": TaskState.TASK_STATE_AUTH_REQUIRED}
# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns. The list
# lives in the checkpointer, so only the current message is used (earlier replies stay out of prompts).
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


def utc_now() -> datetime:
    return datetime.now(UTC)


def user_text(context: RequestContext) -> str:
    """The user's own words: the FIRST text part only. The platform appends org policy and agent
    knowledge as later, unmarked text parts; joining them would make them part of the request."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return texts[0] if texts else ""


def org_policy_text(context: RequestContext) -> str:
    """The later text parts (org policy, agent knowledge): read only for a refund policy paragraph."""
    message = context.message
    texts = get_text_parts(message.parts) if message is not None else []
    return "\n\n".join(texts[1:])


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


class CaseExecutor(AgentExecutor):
    def __init__(self, agent: CaseAgent, llm, model: str, gateway: NasikoMcpClient | None,
                 now: Callable[[], datetime] = utc_now, clock: Callable[[], float] = time.monotonic):
        self._agent = agent
        self._llm = llm
        self._model = model
        self._gateway = gateway
        self._now = now
        self._clock = clock

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:  # only a new task is enqueued whole
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        deps = TurnDeps(llm=self._llm, model=self._model, gateway=self._gateway,
                        traceparent=inbound_traceparent(context), now=self._now(), started=self._clock(),
                        clock=self._clock)
        text = current_message(user_text(context))
        tracer = trace.get_tracer("refund-dispute-handler")
        parent_ctx = request_otel_context.get() or otel_context.Context()
        with tracer.start_as_current_span("refund_dispute.request", context=parent_ctx) as span:
            span.set_attribute("session.id", task.context_id)
            try:
                outcome = await self._run(stored, text, task.context_id, deps, _message_metadata(context),
                                          org_policy_text(context))
            except Exception as e:
                logger.exception("refund-dispute-handler turn failed")
                span.record_exception(e)
                await updater.update_status(TaskState.TASK_STATE_FAILED,
                                            message=updater.new_agent_message([new_text_part(FAILED_TEXT)]))
                return
            await _apply_outcome(updater, outcome)

    async def _run(self, stored, text: str, context_id: str, deps: TurnDeps, message_metadata: Mapping[str, Any],
                   org_text: str) -> walk.Outcome:
        if stored is not None and stored.status.state in _PAUSED_STATES:
            # The paused task's metadata round-trips as a protobuf Struct.
            # Task metadata accumulates across pauses: once the walk has started, the walk wins.
            metadata = MessageToDict(stored.metadata)
            pick = metadata.get(PICK_KEY)
            if walk.METADATA_KEY in metadata:
                outcome = await walk.resume(deps, metadata, text)
            elif DECISION_KEY in metadata:
                outcome = await _outcome_of(deps, await self._agent.decided(text, context_id, deps))
            elif isinstance(pick, Mapping):
                outcome = await self._picked(pick, text, context_id, deps, org_text)
            else:
                outcome = await walk.resume(deps, metadata, text)
        else:
            outcome = await _outcome_of(deps, await self._agent.turn(text, context_id, deps, message_metadata,
                                                                     org_text))
        if outcome.outcomes:
            await self._agent.record_outcomes(context_id, outcome.outcomes)
        return outcome

    async def _picked(self, pick: Mapping[str, Any], text: str, context_id: str, deps: TurnDeps,
                      org_text: str) -> walk.Outcome:
        """The customer or dispute pick: the picked label (or typed text) narrows the original request,
        which then runs as a fresh turn. A skip word ends here."""
        options = {str(k): str(v) for k, v in (pick.get("options") or {}).items()}
        answer = (text or "").strip()
        first = answer.splitlines()[0].strip() if answer else ""
        if not answer or interpret_answer(answer)[0] == "skip":
            return walk.Outcome("complete", NO_PICK_TEXT)
        narrowed = options.get(first) or answer
        query = f"{pick.get('query') or ''} ({narrowed})".strip()
        return await _outcome_of(deps, await self._agent.turn(query, context_id, deps, None, org_text))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _outcome_of(deps: TurnDeps, result: TurnResult) -> walk.Outcome:
    if result.drafts:
        return await walk.start(deps, result.drafts, result.reply)
    question = result.question
    if question is not None:
        text = "\n\n".join(p for p in (result.reply, question.text) if p)
        return walk.Outcome("input_required", text, question.metadata, question.hitl, envelope=result.envelope)
    return walk.Outcome("complete", result.reply or NOTHING_TEXT, envelope=result.envelope)


def _message_metadata(context: RequestContext) -> dict:
    message = context.message
    if message is None or not message.HasField("metadata"):
        return {}
    return MessageToDict(message.metadata)


async def _apply_outcome(updater: TaskUpdater, outcome: walk.Outcome) -> None:
    parts = [new_text_part(outcome.text)]
    if outcome.envelope is not None:  # the list for API clients; MAF reads only the text part
        await updater.add_artifact([*parts, new_data_part(outcome.envelope)], name="refund_case")
    if outcome.kind == "complete":
        if outcome.envelope is None:
            await updater.add_artifact(parts, name="refund_dispute_handler")
        await updater.update_status(TaskState.TASK_STATE_COMPLETED, message=updater.new_agent_message(parts),
                                    metadata=outcome.metadata or None)
        return
    # The pause keys go on the status message: the platform builds the question from
    # `status.message.metadata` and links a gateway approval by its `hitl_request_id`.
    await updater.update_status(_PAUSE_KINDS[outcome.kind],
                                message=updater.new_agent_message(parts, metadata=outcome.hitl),
                                metadata=outcome.metadata)
