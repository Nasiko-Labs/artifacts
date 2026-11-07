"""Make ONE approved write (an issue create or an email reply): a tool loop offered only the approved
tool, every call through a guard. One approval allows one write; a gateway `ask` pauses with the approval
kept, and only `authorized` re-runs the identical call. (Adapted from promise-tracker's sender.)"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from deps import TurnDeps
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
from tool_health import failure_text
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("inbox_to_linear.sender")

WRITE_LOOP_CALLS = 3
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")

WriteStatus = Literal["done", "not_done", "blocked", "denied", "paused"]
Check = Callable[[str, Mapping[str, Any]], "str | None"]


@dataclass(frozen=True)
class WriteResult:
    status: WriteStatus
    reason: str = ""
    output: str = ""                  # the tool's own text on success (issue key and link come from here)
    loop: dict | None = None          # the paused loop, stored on the task (status "paused")
    hitl_request_id: str | None = None


class GuardedGateway:
    """Forwards a call only when `check` passes it as THE approved write. A completed call consumes the
    approval (any later call is refused); a pause keeps it for the identical re-call."""

    def __init__(self, gateway: Any, check: Check):
        self._gateway = gateway
        self._check = check
        self._used = False
        self._results: tuple[ToolResult, ...] = ()

    @property
    def done(self) -> ToolOk | None:
        return next((r for r in self._results if isinstance(r, ToolOk) and failure_text(r) is None), None)

    @property
    def denied(self) -> ToolDenied | None:
        return next((r for r in self._results if isinstance(r, ToolDenied)), None)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        reason = "Refused: one approval covers one write, and it was used." if self._used else self._check(
            name, dict(arguments))
        if reason:
            log.warning("write guard refused %s: %s", name, reason.split(":")[0])
            return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            return result
        self._used = True
        self._results = (*self._results, result)
        return result


async def write_once(deps: TurnDeps, tool: McpTool, messages: tuple[dict, ...], check: Check) -> WriteResult:
    guarded = GuardedGateway(deps.gateway, check)
    return await _run(guarded, lambda: run_tool_loop(deps.llm, deps.model, messages, (tool,), guarded,
                                                     deps.traceparent, max_iterations=WRITE_LOOP_CALLS))


async def resume_write(deps: TurnDeps, tool: McpTool, loop: Mapping[str, Any], answer: str,
                       check: Check) -> WriteResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is a denial."""
    if answer.strip().casefold() != AUTHORIZED:
        return WriteResult("denied", "denied on the approval card")
    guarded = GuardedGateway(deps.gateway, check)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, lambda: resume_tool_loop(deps.llm, deps.model, paused, (tool,), guarded,
                                                        deps.traceparent, max_iterations=WRITE_LOOP_CALLS))


async def _run(guarded: GuardedGateway, loop: Callable[[], Awaitable[LoopResult]]) -> WriteResult:
    try:
        result = await loop()
    except McpGatewayError as e:
        log.warning("write failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if guarded.done is not None:  # the write went out; only the closing LLM call failed
            log.exception("write completed but the closing model call failed")
            return _settled(guarded, "")
        raise
    if result.status in _PAUSED and result.pending is not None:
        return _paused(result)
    return _settled(guarded, "the tool was not called with the approved content")


def _paused(result: LoopResult) -> WriteResult:
    try:
        hitl = approval_pause_metadata(result.pending.result)
    except NoHitlRequestError:
        log.warning("gateway asked for approval without a hitl request id")
        return WriteResult("not_done", "the write needs an approval the platform could not create")
    return WriteResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"])


def _settled(guarded: GuardedGateway, reason: str) -> WriteResult:
    done = guarded.done
    if done is not None:
        return WriteResult("done", output=_original_text(done))
    denied = guarded.denied
    if denied is not None:
        return WriteResult("denied", "denied on the approval card") if denied.by_user else WriteResult("blocked")
    return WriteResult("not_done", reason)


def _original_text(result: ToolOk) -> str:
    content = result.raw.get("content") if isinstance(result.raw, Mapping) else None
    if isinstance(content, list):
        texts = [str(i.get("text", "")) for i in content if isinstance(i, Mapping) and i.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return result.text
