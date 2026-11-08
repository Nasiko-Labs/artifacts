"""Execute ONE approved write step: map the plan step onto the write tool's arguments, pass the send guard,
call the gateway once. No model is involved: Python makes every connector call. A completed call consumes
the approval, so a second write on the same approval is refused before the gateway."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import argmap
import send_guard
import shapes
from codehost import CodeHost
from discovery import Catalog
from llm import TOOL_TIMEOUT_SECS, BudgetSpent, TurnDeps
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    McpTool,
    ToolDenied,
    ToolOk,
)
from send_guard import Approval
from tool_health import failure_text
from writeplan import WritePlan

log = logging.getLogger("issue_to_pr.sender")


@dataclass(frozen=True)
class WriteResult:
    status: str               # written | failed | refused
    reason: str = ""
    url: str | None = None


class GuardedWriter:
    """Forwards a write only when the guard allows it as THE approved step; a completed call consumes it."""

    def __init__(self, deps: TurnDeps, plan: WritePlan, approval: Approval | None):
        self._deps = deps
        self._plan = plan
        self._approval = approval

    async def call(self, tool: McpTool, args: dict, content: str = "") -> WriteResult:
        decision = send_guard.check(self._plan, self._approval, tool, args, content)
        if not decision.allowed:
            log.warning("send guard refused %s: %s", tool.name, decision.reason)
            return WriteResult("refused", decision.reason)
        try:
            self._deps.budget.take_tool_call()
        except BudgetSpent:
            return WriteResult("failed", "the tool-call budget for this turn is spent")
        self._approval = None
        timeout = max(1.0, min(TOOL_TIMEOUT_SECS, self._deps.remaining()))
        try:
            result = await asyncio.wait_for(self._deps.gateway.call_tool(tool.name, args, self._deps.traceparent),
                                            timeout=timeout)
        except (TimeoutError, McpGatewayError) as e:
            return WriteResult("failed", "timed out" if isinstance(e, TimeoutError) else
                               f"the gateway refused the call ({type(e).__name__})")
        return _settle(result)


def _settle(result) -> WriteResult:
    if isinstance(result, (ApprovalPending, AuthorizationRequired)):
        return WriteResult("failed", "the platform asked for another approval or connection, so nothing was written")
    if isinstance(result, ToolDenied):
        return WriteResult("failed", "the platform blocks this write tool for this agent")
    if not isinstance(result, ToolOk) or failure_text(result) is not None:
        why = failure_text(result) if isinstance(result, ToolOk) else None
        return WriteResult("failed", f"the code host returned an error{': ' + why[:160] if why else ''}")
    return WriteResult("written", url=shapes.pr_url(result))


async def execute(deps: TurnDeps, catalog: Catalog, plan: WritePlan, approval: Approval, content: str) -> WriteResult:
    step = plan.next_step
    if step is None:
        return WriteResult("refused", "no step is waiting")
    tool = catalog.write_tool(step.kind)
    if tool is None or tool.name != step.tool:
        return WriteResult("failed", f"the {step.kind} tool shown on the card is no longer granted to this agent")
    base_sha = ""
    if step.kind == "branch" and argmap.needs_base_sha(tool):
        owner, name = step.repo.split("/", 1)
        data = await CodeHost(deps, catalog).call("branch_info", owner=owner, repo=name, ref=step.base)
        base_sha = shapes.commit_sha(data) if data is not None else None
        if not base_sha:
            return WriteResult("failed", f"I couldn't read the latest commit of `{step.base}`")
    args = argmap.build(tool, step, content, base_sha)
    return await GuardedWriter(deps, plan, approval).call(tool, args, content)
