"""Post ONE approved reply (and, if approved, set the ticket solved): a tool loop offered only the approved
write tools, every call through the send guard. A gateway `ask` pauses with the approval and what was
already done kept, so the resumed loop can neither repeat nor exceed it. Adapted from promise-tracker."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import prompts
import send_guard
from approval import Draft
from budget import TurnLimitReached
from deps import TurnDeps
from discovery import discover
from nasiko_hitl import NoHitlRequestError, approval_pause_metadata
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    McpTool,
    ToolDenied,
    ToolOk,
    ToolResult,
)
from send_guard import Approval, Consumed
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("bug_notifier.sender")

SEND_LOOP_CALLS = 3
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")

SendStatus = Literal["sent", "solved", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    loop: dict | None = None
    hitl_request_id: str | None = None
    consumed: Consumed = field(default_factory=Consumed)
    succeeded: Consumed = field(default_factory=Consumed)


class GuardedGateway:
    """Forwards a call only when the send guard allows it. `consumed` blocks a repeat of any change that
    went out or may have (a gateway failure mid-call); `succeeded` is what the desk confirmed. A pause
    changes neither, so the identical re-call is allowed."""

    def __init__(self, gateway: Any, approval: Approval, consumed: Consumed, succeeded: Consumed):
        self._gateway = gateway
        self._approval = approval
        self.consumed = consumed
        self.succeeded = succeeded
        self.denied: ToolDenied | None = None

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self.denied is not None:
            return _refusal("Refused: an earlier change on this ticket was denied. Stop.")
        decision = send_guard.check(name, dict(arguments), self._approval, self.consumed)
        if not decision.allowed or not decision.is_write:
            reason = decision.reason or "Refused: only the approved reply may be posted here."
            log.warning("send guard refused %s: %s", name, reason.split(":")[0])
            return _refusal(reason)
        try:
            result = await self._gateway.call_tool(name, arguments, traceparent)
        except McpGatewayError:
            self.consumed = send_guard.consume(self.consumed, decision.writes)  # outcome unknown: never repeat
            raise
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            return result
        if isinstance(result, ToolDenied):
            self.denied = result
        elif isinstance(result, ToolOk) and not result.is_error:
            self.consumed = send_guard.consume(self.consumed, decision.writes)
            self.succeeded = send_guard.consume(self.succeeded, decision.writes)
        return result


def _refusal(reason: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})


async def send_one(deps: TurnDeps, draft: Draft, approval: Approval) -> SendResult:
    tools, failure = await _send_tools(deps, approval)
    if not tools:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval, Consumed(), Consumed())
    messages = ({"role": "system", "content": prompts.send_system()},
                {"role": "user", "content": _send_request(draft, approval)})
    return await _run(guarded, approval, run_tool_loop(deps.llm, deps.model, messages, tools, guarded,
                                                       deps.traceparent, max_iterations=SEND_LOOP_CALLS))


async def resume_send(deps: TurnDeps, approval: Approval, done: tuple[Consumed, Consumed], loop: Mapping[str, Any],
                      answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is a denial.
    `done` is (consumed, succeeded) as stored at the pause."""
    guarded = GuardedGateway(deps.gateway, approval, *done)
    if answer.strip().casefold() != AUTHORIZED:
        return _settled(guarded, approval, "denied on the approval card", denied=True)
    tools, failure = await _send_tools(deps, approval)
    if not tools:
        return _settled(guarded, approval, failure)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, approval, resume_tool_loop(deps.llm, deps.model, paused, tools, guarded,
                                                          deps.traceparent, max_iterations=SEND_LOOP_CALLS))


async def _send_tools(deps: TurnDeps, approval: Approval) -> tuple[tuple[McpTool, ...], str]:
    catalog, failure = await discover(deps, "send", extra_queries=approval.tools)
    if failure is not None:
        return (), failure
    tools = tuple(t for t in catalog.send_tools() if t.name in approval.tools)
    return tools, "" if tools else "the reply tool is no longer granted to this agent"


async def _run(guarded: GuardedGateway, approval: Approval, loop) -> SendResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("send failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, approval, f"the gateway refused the call ({type(e).__name__})")
    except TurnLimitReached:
        return _settled(guarded, approval, "I ran out of time")
    if result.status in _PAUSED and result.pending is not None:
        return _paused(result, guarded)
    return _settled(guarded, approval, "the reply was not posted")


def _paused(result: LoopResult, guarded: GuardedGateway) -> SendResult:
    try:
        hitl = approval_pause_metadata(result.pending.result)
    except NoHitlRequestError:
        log.warning("gateway asked for approval without a hitl request id")
        return SendResult("not_sent", "posting needs an approval the platform could not create",
                          consumed=guarded.consumed, succeeded=guarded.succeeded)
    return SendResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"],
                      consumed=guarded.consumed, succeeded=guarded.succeeded)


def _settled(guarded: GuardedGateway, approval: Approval, reason: str, denied: bool = False) -> SendResult:
    done, ok = guarded.consumed, guarded.succeeded
    if ok.reply:
        if approval.status and not ok.status:
            return SendResult("sent", "the reply was posted, but the ticket status was not changed",
                              consumed=done, succeeded=ok)
        return SendResult("solved" if ok.status else "sent", consumed=done, succeeded=ok)
    if denied or (guarded.denied is not None and guarded.denied.by_user):
        return SendResult("denied", "denied on the approval card", consumed=done, succeeded=ok)
    if guarded.denied is not None:
        return SendResult("blocked", consumed=done, succeeded=ok)
    return SendResult("not_sent", reason, consumed=done, succeeded=ok)


def _send_request(draft: Draft, approval: Approval) -> str:
    status = (f"Then set the ticket status to {approval.status}." if approval.status
              else "Do not change the ticket status.")
    return (f"Ticket id: {approval.target_id}\nPost the approved text as a PUBLIC reply on this ticket. {status}\n"
            f"Reply text:\n{prompts.fence_approved(approval.body)}")
