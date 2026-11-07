"""Send ONE approved message: a tool loop offered only the approved tool, every call through the
send guard. One approval allows one write; a gateway `ask` pauses with the approval kept.
(Copied in once from the hardened kit; owned by this agent.)"""

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
from drafts import Draft
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
from send_guard import Approval
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("invoice_chaser.sender")

SEND_LOOP_CALLS = 3
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")

SendStatus = Literal["sent", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    loop: dict | None = None          # the paused loop, stored on the task (status "paused")
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
            reason = decision.reason or "Refused: only the approved message may be sent here."
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


async def send_one(deps: TurnDeps, draft: Draft, approval: Approval) -> SendResult:
    tool, failure = await _send_tool(deps, draft)
    if tool is None:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.send_system()},
                {"role": "user", "content": _send_request(draft, approval)})
    return await _run(guarded, run_tool_loop(deps.llm, deps.model, messages, (tool,), guarded, deps.traceparent,
                                             max_iterations=SEND_LOOP_CALLS))


async def resume_send(deps: TurnDeps, draft: Draft, approval: Approval, loop: Mapping[str, Any],
                      answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call. Anything else is a
    denial (fail closed) and nothing is called again."""
    if answer.strip().casefold() != AUTHORIZED:
        return SendResult("denied", "denied on the approval card")
    tool, failure = await _send_tool(deps, draft)
    if tool is None:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, resume_tool_loop(deps.llm, deps.model, paused, (tool,), guarded, deps.traceparent,
                                                max_iterations=SEND_LOOP_CALLS))


async def _send_tool(deps: TurnDeps, draft: Draft) -> tuple[McpTool | None, str]:
    # The approved tool's own name is searched too, so it is found even if it ranks low today.
    catalog, failure = await discover(deps, "send", extra_queries=(draft.tool,))
    if failure is not None:
        return None, failure
    tool = next((t for t in catalog.send_tools() if t.name == draft.tool), None)
    if tool is None:
        return None, "the send tool is no longer granted to this agent"
    return tool, ""


async def _run(guarded: GuardedGateway, loop) -> SendResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("send failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if guarded.sent:  # the write went out; only the closing LLM call failed
            log.exception("send completed but the closing model call failed")
            return SendResult("sent")
        raise
    if result.status in _PAUSED and result.pending is not None:
        return _paused(result)
    return _settled(guarded, "the message was not sent")


def _paused(result: LoopResult) -> SendResult:
    try:
        hitl = approval_pause_metadata(result.pending.result)
    except NoHitlRequestError:
        log.warning("gateway asked for approval without a hitl request id")
        return SendResult("not_sent", "sending needs an approval the platform could not create")
    return SendResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"])


def _settled(guarded: GuardedGateway, reason: str) -> SendResult:
    if guarded.sent:
        return SendResult("sent")
    denied = guarded.denied
    if denied is not None:
        return SendResult("denied", "denied on the approval card") if denied.by_user else SendResult("blocked")
    return SendResult("not_sent", reason)


def _send_request(draft: Draft, approval: Approval) -> str:
    return (f"Send with the tool {draft.tool}.\nRecipient: {approval.recipient}\nSubject: {approval.subject}\n"
            f"Reply in thread: {draft.thread_ref or 'none (new email)'}\n"
            f"Message body:\n{prompts.fence_approved(approval.draft)}")
