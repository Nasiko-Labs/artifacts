"""A2A executor: maps the A2A task lifecycle onto the contract-review graph.

New task → split the message (playbook part = policy, the rest = data) → run the graph.
Task paused (input-required / auth-required) → resume the graph with the answer
(`Command(resume=...)`) on the same thread. A graph interrupt becomes `input-required` whose
`status.message.metadata` carries the structured question or the gateway's `hitl_request_id`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from a2a.helpers import new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from langgraph.types import Command

from graph import build_graph
from llm import client_from_env, model_from_env
from nasiko_mcp import McpGatewayError, NasikoMcpClient
from nasiko_state import make_checkpointer
from nasiko_trace import extract_traceparent
from policy import attached_text, split_history, split_message, split_pasted_contract, text_parts, wants_per_break

log = logging.getLogger("contract_review.executor")

PAUSE_MODE_ENV = "CONTRACT_PAUSE_MODE"
PER_BREAK = "per_break"
SINGLE = "single"
LLM_HTTP_TIMEOUT_SECS = 120.0
_PAUSED = (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED)
LOST_STATE = ("I no longer have the paused review (the agent restarted without durable state). "
              "Please ask me to review the contract again.")
NO_TRACEPARENT = "This request carried no traceparent, so I can't use the workspace or the model router."


def pause_mode(request: str, env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    if (source.get(PAUSE_MODE_ENV) or "").strip().lower() == PER_BREAK or wants_per_break(request):
        return PER_BREAK
    return SINGLE


def initial_state(message: Any, env: Mapping[str, str] | None = None) -> dict:
    """The contract comes from an attached text file, else from text pasted under the request;
    with neither, `read_contract` reads it from the workspace."""
    incoming = split_message(text_parts(message))
    request, pasted = split_pasted_contract(incoming.request)
    state = {"request": request, "rules": list(incoming.playbook_rules),
             "org_policy": incoming.org_policy, "pause_mode": pause_mode(request, env)}
    contract = attached_text(message) or pasted
    return {**state, "msa": contract} if contract else state


class ContractReviewExecutor(AgentExecutor):
    def __init__(self, checkpointer: Any = None, gateway: Any = None, llm: Any = None, model: str | None = None):
        self.graph = build_graph(checkpointer if checkpointer is not None else make_checkpointer())
        self.llm = llm or client_from_env(httpx.AsyncClient(timeout=LLM_HTTP_TIMEOUT_SECS))
        self.model = model or model_from_env()
        self.gateway = gateway if gateway is not None else NasikoMcpClient.from_env()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        stored = context.current_task
        task = stored or new_task_from_user_message(context.message)
        if stored is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        try:
            outcome = await self._run(context, task.id, stored is not None and stored.status.state in _PAUSED)
        except Exception as e:  # surface as failed, never a hung task
            log.exception("task %s failed", task.id)
            await updater.failed(updater.new_agent_message([new_text_part(f"The contract review failed ({type(e).__name__}). Nothing was written.")]))
            return
        await _apply(updater, outcome)

    async def _run(self, context: RequestContext, task_id: str, is_resume: bool) -> dict:
        traceparent = extract_traceparent(context)
        if not traceparent:
            return {"reply": NO_TRACEPARENT}
        config = {"configurable": {
            "thread_id": task_id, "llm": self.llm, "model": self.model, "gateway": self.gateway,
            "tools": await self._tools(traceparent), "traceparent": traceparent,
            "today": datetime.now(UTC).date().isoformat(),
        }}
        if is_resume:
            snapshot = await self.graph.aget_state(config)
            if not snapshot.next:
                return {"reply": LOST_STATE}
            parts = text_parts(context.message)
            _, answer = split_history(parts[0] if parts else "")
            result = await self.graph.ainvoke(Command(resume=answer.strip()), config)
        else:
            result = await self.graph.ainvoke(initial_state(context.message), config)
        return result

    async def _tools(self, traceparent: str) -> tuple:
        if self.gateway is None:
            return ()
        try:
            listed = await self.gateway.list_tools(traceparent)
        except McpGatewayError as e:
            log.warning("tools/list failed: %s", type(e).__name__)
            return ()
        return listed

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is not None:
            await TaskUpdater(event_queue, task.id, task.context_id).cancel()


async def _apply(updater: TaskUpdater, result: Mapping[str, Any]) -> None:
    interrupts = result.get("__interrupt__") or ()
    if interrupts:
        payload = interrupts[0].value
        await updater.update_status(
            TaskState.TASK_STATE_INPUT_REQUIRED,
            message=updater.new_agent_message([new_text_part(payload["text"])], metadata=payload.get("metadata")),
        )
        return
    text = str(result.get("reply") or "Done.")
    await updater.add_artifact([new_text_part(text)], name="contract_review")
    await updater.complete(message=updater.new_agent_message([new_text_part(text)]))
