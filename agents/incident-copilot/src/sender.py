"""Post ONE approved card: a tool loop offered only the approved post tool, every call through the send
guard (same tool, recipient = the approved channel id, body = the approved text exactly, one write). A
gateway `ask` pauses with the approval kept; only `authorized` re-runs the identical call."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import prompts
import send_guard
from deps import TurnDeps
from discovery import discover
from nasiko_hitl import NoHitlRequestError, approval_pause_metadata
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    McpTool,
    NasikoMcpClient,
    ToolDenied,
    ToolOk,
    ToolResult,
)
from posting import PostPlan
from send_guard import Approval
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("incident_copilot.sender")

POST_LOOP_CALLS = 3
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")

PostStatus = Literal["posted", "not_posted", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class PostResult:
    status: PostStatus
    reason: str = ""
    loop: dict | None = None
    hitl_request_id: str | None = None


class GuardedGateway:
    """Forwards a call only when the send guard allows it as THE approved write. A completed call
    (ok, error or denied) consumes the approval; a pause keeps it for the identical re-call."""

    def __init__(self, gateway: NasikoMcpClient, approval: Approval):
        self._gateway = gateway
        self._approval: Approval | None = approval
        self._results: tuple[ToolResult, ...] = ()

    @property
    def sent(self) -> bool:
        return any(isinstance(r, ToolOk) and not r.is_error for r in self._results)

    @property
    def denied(self) -> ToolDenied | None:
        return next((r for r in self._results if isinstance(r, ToolDenied)), None)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        decision = send_guard.check([name], [dict(arguments)], self._approval)
        if not decision.allowed or not decision.is_write:
            reason = decision.reason or "Refused: only the approved card may be posted here."
            log.warning("send guard refused %s: %s", name, reason.split(":")[0])
            return _refusal(reason)
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            return result
        self._approval = None
        self._results = (*self._results, result)
        return result


def _refusal(reason: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})


def approval_for(plan: PostPlan, text: str) -> Approval:
    return Approval(action="post", target=plan.channel_name, draft=text, tool=plan.tool, recipient=plan.channel_id)


async def post_one(deps: TurnDeps, plan: PostPlan, approval: Approval) -> PostResult:
    tool, failure = await _post_tool(deps, plan)
    if tool is None:
        return PostResult("not_posted", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.post_system()},
                {"role": "user", "content": f"Post with the tool {plan.tool}.\nChannel id: {plan.channel_id}\n"
                                            f"Message text:\n{prompts.fence_approved(approval.draft)}"})
    return await _run(guarded, run_tool_loop(deps.llm, deps.model, messages, (tool,), guarded, deps.traceparent,
                                             max_iterations=POST_LOOP_CALLS))


async def resume_post(deps: TurnDeps, plan: PostPlan, approval: Approval, loop: Mapping[str, Any],
                      answer: str) -> PostResult:
    if answer.strip().casefold() != AUTHORIZED:
        return PostResult("denied", "denied on the approval card")
    tool, failure = await _post_tool(deps, plan)
    if tool is None:
        return PostResult("not_posted", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, resume_tool_loop(deps.llm, deps.model, paused, (tool,), guarded, deps.traceparent,
                                                max_iterations=POST_LOOP_CALLS))


async def _post_tool(deps: TurnDeps, plan: PostPlan) -> tuple[McpTool | None, str]:
    catalog, failure = await discover(deps, "post", extra_queries=(plan.tool,))
    if failure is not None:
        return None, failure
    tool = next((t for t in catalog.write_tools() if t.name == plan.tool), None)
    return (tool, "") if tool else (None, "the post tool is no longer granted to this agent")


async def _run(guarded: GuardedGateway, loop) -> PostResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("post failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if guarded.sent:  # the write went out; only the closing LLM call failed
            log.exception("post completed but the closing model call failed")
            return PostResult("posted")
        raise
    if result.status in _PAUSED and result.pending is not None:
        try:
            hitl = approval_pause_metadata(result.pending.result)
        except NoHitlRequestError:
            return PostResult("not_posted", "posting needs an approval the platform could not create")
        return PostResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"])
    return _settled(guarded, "the card was not posted")


def _settled(guarded: GuardedGateway, reason: str) -> PostResult:
    if guarded.sent:
        return PostResult("posted")
    denied = guarded.denied
    if denied is not None:
        return PostResult("denied", "denied on the approval card") if denied.by_user else PostResult("blocked")
    return PostResult("not_posted", reason)
