"""Make ONE approved write: a tool loop offered only the approved tool (plus, for a CRM note, the one
association tool), every call through the send guard. A gateway `ask` pauses with the approval kept."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import prompts
import send_guard
from deps import TurnDeps
from discovery import Step, discover
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

log = logging.getLogger("call_miner.sender")

LOOP_CALLS = {"note": 4, "post": 3}
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")
_IDS = re.compile(r'"(?:id|noteId|note_id|hs_object_id|objectId)"\s*:\s*"?([\w-]+)"?')

SendStatus = Literal["sent", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    loop: dict | None = None
    hitl_request_id: str | None = None


class GuardedGateway:
    """Forwards a call only when the send guard allows it as THE approved write. A successful write
    consumes the approval (a note's create step hands over to its association step); an error result
    leaves it for a corrected retry; a pause keeps it for the identical re-call."""

    def __init__(self, gateway: Any, approval: Approval):
        self._gateway = gateway
        self._approval: Approval | None = approval
        self._created: frozenset[str] = frozenset()
        self._results: tuple[ToolResult, ...] = ()

    @property
    def sent(self) -> bool:
        return any(isinstance(r, ToolOk) and not r.is_error for r in self._results)

    @property
    def denied(self) -> ToolDenied | None:
        return next((r for r in self._results if isinstance(r, ToolDenied)), None)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        decision = send_guard.check([name], [dict(arguments)], self._approval, self._created)
        if not decision.allowed or not decision.is_write:
            reason = decision.reason or "Refused: only the approved write may be made here."
            log.warning("send guard refused %s: %s", name, reason.split(":")[0])
            return _refusal(reason)
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            return result
        self._results = (*self._results, result)
        if isinstance(result, ToolOk) and result.is_error:
            return result
        approval = self._approval
        if approval and approval.kind == "note" and approval.assoc_tool and not self._created:
            self._created = frozenset(_IDS.findall(result.text if isinstance(result, ToolOk) else "")) or frozenset()
            self._approval = approval if self._created else None
        else:
            self._approval = None
        return result


def _refusal(reason: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})


async def send_one(deps: TurnDeps, step: Step, approval: Approval, request: str) -> SendResult:
    tools, failure = await _tools(deps, step, approval)
    if not tools:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.send_system(approval.action)},
                {"role": "user", "content": f"{request}\n\n{prompts.fence_approved(approval.draft)}"})
    return await _run(guarded, run_tool_loop(deps.llm, deps.model, messages, tools, guarded, deps.traceparent,
                                             max_iterations=LOOP_CALLS.get(approval.kind, 3)))


async def resume_send(deps: TurnDeps, step: Step, approval: Approval, loop: Mapping[str, Any],
                      answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is denied."""
    if answer.strip().casefold() != AUTHORIZED:
        return SendResult("denied", "denied on the approval card")
    tools, failure = await _tools(deps, step, approval)
    if not tools:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, resume_tool_loop(deps.llm, deps.model, paused, tools, guarded, deps.traceparent,
                                                max_iterations=LOOP_CALLS.get(approval.kind, 3)))


async def _tools(deps: TurnDeps, step: Step, approval: Approval) -> tuple[tuple[McpTool, ...], str]:
    names = [n for n in (approval.tool, approval.assoc_tool) if n]
    catalog, failure = await discover(deps, step, extra=names)
    if failure is not None:
        return (), failure
    tools = tuple(t for t in (catalog.named(n) for n in names) if t is not None)
    if not tools or tools[0].name != approval.tool:
        return (), "the write tool is no longer granted to this agent"
    return tools, ""


async def _run(guarded: GuardedGateway, loop) -> SendResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("write failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if guarded.sent:  # the write went out; only the closing LLM call failed
            log.exception("write completed but the closing model call failed")
            return SendResult("sent")
        raise
    if result.status in _PAUSED and result.pending is not None:
        try:
            hitl = approval_pause_metadata(result.pending.result)
        except NoHitlRequestError:
            log.warning("gateway asked for approval without a hitl request id")
            return SendResult("not_sent", "the write needs an approval the platform could not create")
        return SendResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"])
    return _settled(guarded, "the write was not made")


def _settled(guarded: GuardedGateway, reason: str) -> SendResult:
    if guarded.sent:
        return SendResult("sent")
    denied = guarded.denied
    if denied is not None:
        return SendResult("denied", "denied on the approval card") if denied.by_user else SendResult("blocked")
    return SendResult("not_sent", reason)
