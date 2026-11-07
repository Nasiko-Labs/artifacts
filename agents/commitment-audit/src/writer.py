"""Write ONE approved rollup: a tool loop offered only the approved tool, every call through the send
guard. One approval allows one write; a gateway `ask` pauses with the approval kept."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import prompts
import send_guard
from approval import WriteRequest
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
from send_guard import Approval
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("commitment_audit.writer")

WRITE_LOOP_CALLS = 3
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")

WriteStatus = Literal["written", "not_written", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class WriteResult:
    status: WriteStatus
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
    def written(self) -> bool:
        return any(isinstance(r, ToolOk) and not r.is_error for r in self._results)

    @property
    def denied(self) -> ToolDenied | None:
        return next((r for r in self._results if isinstance(r, ToolDenied)), None)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        decision = send_guard.check([name], [dict(arguments)], self._approval)
        if not decision.allowed or not decision.is_write:
            reason = decision.reason or "Refused: only the approved rollup may be written here."
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


async def write_one(deps: TurnDeps, request: WriteRequest, approval: Approval) -> WriteResult:
    tool, failure = await _write_tool(deps, request)
    if tool is None:
        return WriteResult("not_written", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.write_system()},
                {"role": "user", "content": _write_request(request, approval)})
    return await _run(guarded, run_tool_loop(deps.llm, deps.model, messages, (tool,), guarded, deps.traceparent,
                                             max_iterations=WRITE_LOOP_CALLS))


async def resume_write(deps: TurnDeps, request: WriteRequest, approval: Approval, loop: Mapping[str, Any],
                       answer: str) -> WriteResult:
    """After the platform's card: only `authorized` re-runs the identical call. Anything else is a
    denial (fail closed) and nothing is called again."""
    if answer.strip().casefold() != AUTHORIZED:
        return WriteResult("denied", "denied on the approval card")
    tool, failure = await _write_tool(deps, request)
    if tool is None:
        return WriteResult("not_written", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, resume_tool_loop(deps.llm, deps.model, paused, (tool,), guarded, deps.traceparent,
                                                max_iterations=WRITE_LOOP_CALLS))


async def _write_tool(deps: TurnDeps, request: WriteRequest) -> tuple[McpTool | None, str]:
    # The approved tool's own name is searched too, so it is found even if it ranks low today.
    purpose = "write_doc" if request.kind == "doc" else "write_chat"
    catalog, failure = await discover(deps, purpose, extra_queries=(request.tool,))
    if failure is not None:
        return None, failure
    tool = next((t for t in catalog.tools if t.name == request.tool and send_guard.is_write_tool(t.name)), None)
    if tool is None:
        return None, "the write tool is no longer granted to this agent"
    return tool, ""


async def _run(guarded: GuardedGateway, loop) -> WriteResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("write failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if guarded.written:  # the write went out; only the closing LLM call failed
            log.exception("write completed but the closing model call failed")
            return WriteResult("written")
        raise
    if result.status in _PAUSED and result.pending is not None:
        return _paused(result)
    return _settled(guarded, "the rollup was not written")


def _paused(result: LoopResult) -> WriteResult:
    try:
        hitl = approval_pause_metadata(result.pending.result)
    except NoHitlRequestError:
        log.warning("gateway asked for approval without a hitl request id")
        return WriteResult("not_written", "writing needs an approval the platform could not create")
    return WriteResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"])


def _settled(guarded: GuardedGateway, reason: str) -> WriteResult:
    if guarded.written:
        return WriteResult("written")
    denied = guarded.denied
    if denied is not None:
        return WriteResult("denied", "denied on the approval card") if denied.by_user else WriteResult("blocked")
    return WriteResult("not_written", reason)


def _write_request(request: WriteRequest, approval: Approval) -> str:
    target = (f"Create a new document titled: {approval.subject}" if request.kind == "doc"
              else f"Post one message to the channel with id: {approval.recipient}")
    return f"Write with the tool {request.tool}.\n{target}\nText:\n{prompts.fence_rollup(approval.draft)}"
