"""Make ONE approved nudge: the arguments are filled in code from the comment tool's own schema with the
approved destination and body, and the call goes through the send guard. One approval allows one
write; a gateway `ask` pauses with the approval and the exact arguments kept for the identical re-call.
No model is involved in the write."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import callers
import records
import send_guard
from deps import TurnDeps
from discovery import Discoverer
from nasiko_hitl import NoHitlRequestError, approval_pause_metadata
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    ToolDenied,
    ToolOk,
    ToolResult,
)
from nudge import Proposal
from send_guard import Approval

log = logging.getLogger("pr_review_queue.send")

AUTHORIZED = "authorized"
SendStatus = Literal["sent", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    args: dict | None = None
    hitl_request_id: str | None = None
    url: str = ""


class GuardedGateway:
    """Forwards a call only when the send guard allows it as THE approved write."""

    def __init__(self, gateway: Any, approval: Approval):
        self._gateway = gateway
        self._approval = approval

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        decision = send_guard.check([name], [dict(arguments)], self._approval)
        if not decision.allowed or not decision.is_write:
            reason = decision.reason or "Refused: only the approved write may be made here."
            log.warning("send guard refused %s: %s", name, reason.split(":")[0])
            return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})
        return await self._gateway.call_tool(name, arguments, traceparent)


async def send_one(deps: TurnDeps, p: Proposal, approval: Approval) -> SendResult:
    tool, failure = await _write_tool(deps, p)
    if tool is None:
        return SendResult("not_sent", failure)
    args = callers.fill(tool, {"owner": p.owner, "repo": p.repo, "number": p.number, "body": approval.draft})
    if args is None:
        return SendResult("not_sent", "the comment tool needs arguments I can't fill from checked data")
    return await _call(deps, approval, tool.name, args)


async def resume_send(deps: TurnDeps, p: Proposal, approval: Approval, args: Mapping[str, Any],
                      answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is a denial."""
    if answer.strip().casefold() != AUTHORIZED:
        return SendResult("denied", "denied on the approval card")
    return await _call(deps, approval, p.tool, dict(args))


async def _write_tool(deps: TurnDeps, p: Proposal):
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return None, failure
    catalog = await discoverer.catalog("nudge")
    tool = next((t for t in catalog.write_tools() if t.name == p.tool), None)
    return (tool, "") if tool else (None, "the comment tool is no longer granted to this agent")


async def _call(deps: TurnDeps, approval: Approval, name: str, args: dict) -> SendResult:
    guarded = GuardedGateway(deps.gateway, approval)
    try:
        result = await guarded.call_tool(name, args, deps.traceparent)
    except McpGatewayError as e:
        log.warning("write failed at the gateway: %s", type(e).__name__)
        return SendResult("not_sent", f"the gateway refused the call ({type(e).__name__})")
    if isinstance(result, (ApprovalPending, AuthorizationRequired)):
        try:
            hitl = approval_pause_metadata(result)
        except NoHitlRequestError:
            return SendResult("not_sent", "writing needs an approval the platform could not create")
        return SendResult("paused", args=args, hitl_request_id=hitl["hitl_request_id"])
    if isinstance(result, ToolDenied):
        return SendResult("denied", "denied on the approval card") if result.by_user else SendResult("blocked")
    if result.is_error:
        return SendResult("not_sent", _error_reason(result.text))
    return SendResult("sent", url=_link(result.text))


def _error_reason(text: str) -> str:
    data = records.parse_json(text)
    if isinstance(data, Mapping) and isinstance(data.get("error"), str):
        return data["error"][:200]
    return "the comment tool returned an error"


def _link(text: str) -> str:
    for o in records.objects(text):
        url = records.s(o.get("html_url"))
        if url.startswith("https://"):
            return url
    return ""
