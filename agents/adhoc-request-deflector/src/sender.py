"""Post ONE approved reply. Code builds the call from the post tool's own schema (channel, thread, text)
and the send guard checks it against the approval before it reaches the gateway; the model is not
involved. A completed call consumes the approval; a gateway `ask` pauses with the call kept, and only
`authorized` re-runs that identical call. (Adapted from the MCP kit; owned here.)"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import send_guard
import toolargs
from deps import TurnDeps
from discovery import discover
from fence import error_body
from nasiko_hitl import NoHitlRequestError, approval_pause_metadata
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    ToolDenied,
    ToolOk,
)
from send_guard import Approval

log = logging.getLogger("deflector.sender")

AUTHORIZED = "authorized"
_MAX_REASON_CHARS = 120

SendStatus = Literal["posted", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    arguments: dict | None = None      # the paused call, stored on the task (status "paused")
    hitl_request_id: str | None = None


async def post_one(deps: TurnDeps, approval: Approval) -> SendResult:
    if not deps.traceparent or deps.gateway is None:
        return SendResult("not_sent", "no trace context or gateway for this request")
    catalog, failure = await discover(deps, ("reply",))
    tool = catalog.role("post") if catalog else None
    if tool is None or tool.name != approval.tool:
        return SendResult("not_sent", failure or "the post tool is no longer available to this agent")
    args = toolargs.post_args(tool, approval.channel, approval.thread, approval.draft)
    if args is None:
        return SendResult("not_sent", "the post tool can't reply in a thread")
    return await _call(deps, approval, args)


async def resume_post(deps: TurnDeps, approval: Approval, arguments: Mapping[str, Any], answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is a denial."""
    if answer.strip().casefold() != AUTHORIZED:
        return SendResult("denied", "denied on the approval card")
    return await _call(deps, approval, dict(arguments))


async def _call(deps: TurnDeps, approval: Approval, args: dict) -> SendResult:
    decision = send_guard.check(approval.tool, args, approval)
    if not decision.allowed:
        log.warning("send guard refused %s: %s", approval.tool, decision.reason.split(":")[0])
        return SendResult("not_sent", decision.reason)
    try:
        result = await deps.gateway.call_tool(approval.tool, args, deps.traceparent)
    except McpGatewayError as e:
        log.warning("post failed at the gateway: %s", type(e).__name__)
        return SendResult("not_sent", f"the gateway refused the call ({type(e).__name__})")
    if isinstance(result, (ApprovalPending, AuthorizationRequired)):
        try:
            hitl = approval_pause_metadata(result)
        except NoHitlRequestError:
            return SendResult("not_sent", "posting needs an approval the platform could not create")
        return SendResult("paused", arguments=args, hitl_request_id=hitl["hitl_request_id"])
    if isinstance(result, ToolDenied):
        return SendResult("denied", "denied on the approval card") if result.by_user else SendResult("blocked")
    if isinstance(result, ToolOk) and not result.is_error and not error_body(result.text):
        return SendResult("posted")
    text = result.text if isinstance(result, ToolOk) else ""
    return SendResult("not_sent", " ".join(text.split())[:_MAX_REASON_CHARS] or "the post failed")
