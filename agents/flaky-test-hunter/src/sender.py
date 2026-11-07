"""Make ONE approved write: a tool loop offered only the approved tool, every call through the send guard
(adapted from the ci-failure-detective's sender; owned here). A successful call consumes the approval, so
a second write in the same loop is refused before the gateway; a failed call does not, so the model may
correct it once within the loop's call cap. A gateway `ask` pauses with the approval kept."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import prompts
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
from send_guard import Approval
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("flaky_hunter.sender")

SEND_LOOP_CALLS = 3
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")
STEP_OF = {"issue": "ticket", "comment": "ticket", "branch": "pr", "commit": "pr", "pr": "pr"}

SendStatus = Literal["sent", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    text: str = ""                 # the write's raw result (the created issue, ref, file or PR)
    loop: dict | None = None
    hitl_request_id: str | None = None


class GuardedGateway:
    """Forwards a call only when the send guard allows it as THE approved write."""

    def __init__(self, gateway: Any, approval: Approval):
        self._gateway = gateway
        self._approval: Approval | None = approval
        self._results: tuple[ToolResult, ...] = ()

    @property
    def sent(self) -> ToolOk | None:
        return next((r for r in self._results if isinstance(r, ToolOk) and not r.is_error), None)

    @property
    def denied(self) -> ToolDenied | None:
        return next((r for r in self._results if isinstance(r, ToolDenied)), None)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        decision = send_guard.check(name, dict(arguments), self._approval)
        if not decision.allowed or not decision.is_write:
            reason = decision.reason or "Refused: only the approved write may be made here."
            log.warning("send guard refused %s: %s", name, reason.split(":")[0])
            return _refusal(reason)
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            return result
        self._results = (*self._results, result)
        if (isinstance(result, ToolOk) and not result.is_error) or isinstance(result, ToolDenied):
            self._approval = None
        return result


def _refusal(reason: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})


async def send_one(deps: TurnDeps, approval: Approval) -> SendResult:
    tool, failure = await _write_tool(deps, approval)
    if tool is None:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.send_system()},
                {"role": "user", "content": _request(approval)})
    return await _run(guarded, run_tool_loop(deps.llm, deps.model, messages, (tool,), guarded, deps.traceparent,
                                             max_iterations=SEND_LOOP_CALLS))


async def resume_send(deps: TurnDeps, approval: Approval, loop: Mapping[str, Any], answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is a denial."""
    if answer.strip().casefold() != AUTHORIZED:
        return SendResult("denied", "denied on the approval card")
    tool, failure = await _write_tool(deps, approval)
    if tool is None:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, resume_tool_loop(deps.llm, deps.model, paused, (tool,), guarded, deps.traceparent,
                                                max_iterations=SEND_LOOP_CALLS))


async def _write_tool(deps: TurnDeps, approval: Approval):
    if not deps.traceparent:
        return None, "this request arrived without a trace context"
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return None, failure
    catalog = await discoverer.catalog(STEP_OF[approval.kind])
    tool = next((t for t in catalog.write_tools() if t.name == approval.tool), None)
    return (tool, "") if tool else (None, "the write tool is no longer granted to this agent")


async def _run(guarded: GuardedGateway, loop) -> SendResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("write failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if guarded.sent is not None:  # the write went out; only the closing LLM call failed
            log.exception("write completed but the closing model call failed")
            return SendResult("sent", text=guarded.sent.text)
        raise
    if result.status in _PAUSED and result.pending is not None:
        return _paused(result)
    return _settled(guarded, "the write was not made")


def _paused(result: LoopResult) -> SendResult:
    try:
        hitl = approval_pause_metadata(result.pending.result)
    except NoHitlRequestError:
        log.warning("gateway asked for approval without a hitl request id")
        return SendResult("not_sent", "writing needs an approval the platform could not create")
    return SendResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"])


def _settled(guarded: GuardedGateway, reason: str) -> SendResult:
    if guarded.sent is not None:
        return SendResult("sent", text=guarded.sent.text)
    denied = guarded.denied
    if denied is not None:
        return SendResult("denied", "denied on the approval card") if denied.by_user else SendResult("blocked")
    return SendResult("not_sent", reason)


def _request(approval: Approval) -> str:
    values = "\n".join(f"{k}: {v}" for k, v in approval.values.items() if k not in ("content", "body") and v)
    lines = [f"Write with the tool {approval.tool}.", f"Owner: {approval.owner}", f"Repository: {approval.repo}", values]
    for key in ("body", "content"):
        if approval.values.get(key):
            lines.append(f"{key} (verbatim):\n{prompts.fence_approved(approval.values[key])}")
    return "\n".join(line for line in lines if line)
