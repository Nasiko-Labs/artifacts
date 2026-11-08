"""Post ONE approved retro to ONE channel: a tool loop offered the approved post tools plus chat lookups (to
turn the channel name into an id), every call through the send guard. One approval allows one post; a
gateway `ask` pauses with the approval, what was read and whether the post went out kept, so the resumed
loop can neither repeat nor exceed it. Copied in from an earlier agent and adapted; owned here."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import prompts
import send_guard
from budget import TurnLimitReached
from deps import TurnDeps
from discovery import discover
from fence import FencedGateway
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
from send_guard import Approval
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("retro.sender")

SEND_LOOP_CALLS = 3
AUTHORIZED = "authorized"
MAX_SEEN_KEPT = 24_000
_PAUSED = ("approval_pending", "authorization_required")

SendStatus = Literal["sent", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    loop: dict | None = None
    hitl_request_id: str | None = None
    consumed: bool = False
    seen_text: str = ""


class GuardedGateway:
    """Reads (channel lookups) pass through and are recorded; a write is forwarded only when the guard allows
    it as THE approved post. `consumed` blocks any second post, also after a failure with unknown outcome."""

    def __init__(self, gateway: Any, approval: Approval, consumed: bool = False, seen_text: str = ""):
        self._gateway = gateway
        self._reads = FencedGateway(gateway)
        self._earlier = seen_text
        self._approval = approval
        self.consumed = consumed
        self.sent = False
        self.denied: ToolDenied | None = None

    @property
    def seen_text(self) -> str:
        return "\n".join(t for t in (self._earlier, self._reads.seen_text) if t)[-MAX_SEEN_KEPT:]

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        decision = send_guard.check(name, dict(arguments), self._approval, self.consumed, self.seen_text)
        if not decision.allowed:
            log.warning("send guard refused %s: %s", name, decision.reason.split(":")[0])
            return _refusal(decision.reason)
        if not decision.is_write:
            return await self._reads.call_tool(name, arguments, traceparent)
        try:
            result = await self._gateway.call_tool(name, arguments, traceparent)
        except McpGatewayError:
            self.consumed = True  # outcome unknown: never post twice
            raise
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            return result
        if isinstance(result, ToolDenied):
            self.denied, self.consumed = result, True
        elif isinstance(result, ToolOk) and not result.is_error:
            self.sent, self.consumed = True, True
        return result


def _refusal(reason: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})


async def send_one(deps: TurnDeps, approval: Approval) -> SendResult:
    tools, failure = await _send_tools(deps, approval)
    if not tools:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.send_system()},
                {"role": "user", "content": f"Channel: #{approval.channel}\nApproved message:\n"
                                            f"{prompts.fence_approved(approval.body)}"})
    return await _run(guarded, run_tool_loop(deps.llm, deps.model, messages, tools, guarded, deps.traceparent,
                                             max_iterations=SEND_LOOP_CALLS))


async def resume_send(deps: TurnDeps, approval: Approval, consumed: bool, seen_text: str, loop: Mapping[str, Any],
                      answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is a denial."""
    if answer.strip().casefold() != AUTHORIZED:
        return SendResult("denied", "denied on the approval card", consumed=True)
    tools, failure = await _send_tools(deps, approval)
    if not tools:
        return SendResult("not_sent", failure, consumed=consumed)
    guarded = GuardedGateway(deps.gateway, approval, consumed, seen_text)
    return await _run(guarded, resume_tool_loop(deps.llm, deps.model, LoopResult.from_metadata(loop), tools, guarded,
                                                deps.traceparent, max_iterations=SEND_LOOP_CALLS))


async def _send_tools(deps: TurnDeps, approval: Approval) -> tuple[tuple[McpTool, ...], str]:
    catalog, failure = await discover(deps, "post", extra_queries=approval.tools)
    if failure is not None:
        return (), failure
    writes = tuple(t for t in catalog.send_tools() if t.name in approval.tools)
    if not writes:
        return (), "the post tool is no longer granted to this agent"
    lookups = tuple(t for t in catalog.read_tools() if catalog.source_of(t) in ("chat", None))
    return (*writes, *lookups), ""


async def _run(guarded: GuardedGateway, loop) -> SendResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("post failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except TurnLimitReached:
        return _settled(guarded, "I ran out of time")
    if result.status in _PAUSED and result.pending is not None:
        try:
            hitl = approval_pause_metadata(result.pending.result)
        except NoHitlRequestError:
            return SendResult("not_sent", "posting needs an approval the platform could not create",
                              consumed=guarded.consumed)
        return SendResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"],
                          consumed=guarded.consumed, seen_text=guarded.seen_text)
    return _settled(guarded, "the retro was not posted")


def _settled(guarded: GuardedGateway, reason: str) -> SendResult:
    if guarded.sent:
        return SendResult("sent", consumed=True)
    if guarded.denied is not None:
        status: SendStatus = "denied" if guarded.denied.by_user else "blocked"
        return SendResult(status, "denied on the approval card" if status == "denied" else "", consumed=True)
    return SendResult("not_sent", reason, consumed=guarded.consumed)
