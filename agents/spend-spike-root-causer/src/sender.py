"""Post ONE approved chat message: a tool loop offered only the approved tool, every call through the send guard.
One approval allows one write; a gateway `ask` pauses with the approval kept. (Adapted from the MCP kit's sender;
owned by this agent.)"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

import prompts
import send_guard
from deps import LLM_CALL_TIMEOUT_SECS, TurnDeps
from discovery import DiscoveryFailure, discover
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

log = logging.getLogger("spend_spike.sender")

SEND_LOOP_CALLS = 3
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")

SendStatus = Literal["sent", "not_sent", "blocked", "denied", "paused"]


@dataclass(frozen=True)
class Post:
    """One message to one chat target, through one tool. `recipient` is the channel or user id the write names."""

    target: str
    recipient: str
    body: str
    tool: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Any) -> Post | None:
        if not isinstance(data, Mapping) or not data.get("recipient") or not data.get("body"):
            return None
        return Post(**{f.name: str(data.get(f.name) or "") for f in fields(Post)})


@dataclass(frozen=True)
class SendResult:
    status: SendStatus
    reason: str = ""
    loop: dict | None = None          # the paused loop, stored on the task (status "paused")
    hitl_request_id: str | None = None


def approval_for(post: Post, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this channel or user, this body."""
    return Approval(action="post", target=post.target, draft=body or post.body, tool=post.tool,
                    recipient=post.recipient)


class GuardedGateway:
    """Forwards a call only when the send guard allows it as THE approved write. A completed call (ok, error or
    denied) consumes the approval; a pause keeps it for the identical re-call."""

    def __init__(self, gateway: Any, approval: Approval):
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
            reason = decision.reason or "Refused: only the approved message may be posted here."
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


async def find_send_tool(deps: TurnDeps, wanted: str | None = None) -> tuple[McpTool | None, str]:
    """The chat post tool for this request (`wanted` = the approved tool's name), or (None, why not)."""
    try:
        catalog = await discover(deps, "send", extra_queries=(wanted,) if wanted else ())
    except DiscoveryFailure as e:
        return None, str(e)
    tools = catalog.send_tools()
    tool = next((t for t in tools if t.name == wanted), None) if wanted else (tools[0] if tools else None)
    if tool is None:
        return None, "the chat post tool is not granted to this agent"
    return tool, ""


async def send_one(deps: TurnDeps, post: Post, approval: Approval) -> SendResult:
    tool, failure = await find_send_tool(deps, post.tool)
    if tool is None:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.send_system()},
                {"role": "user", "content": _send_request(post, approval)})
    return await _run(guarded, run_tool_loop(deps.llm, deps.model, messages, (tool,), guarded, deps.traceparent,
                                             max_iterations=SEND_LOOP_CALLS, timeout=LLM_CALL_TIMEOUT_SECS))


async def resume_send(deps: TurnDeps, post: Post, approval: Approval, loop: Mapping[str, Any],
                      answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call. Anything else is a denial (fail
    closed) and nothing is called again."""
    if answer.strip().casefold() != AUTHORIZED:
        return SendResult("denied", "denied on the approval card")
    tool, failure = await find_send_tool(deps, post.tool)
    if tool is None:
        return SendResult("not_sent", failure)
    guarded = GuardedGateway(deps.gateway, approval)
    paused = LoopResult.from_metadata(loop)
    return await _run(guarded, resume_tool_loop(deps.llm, deps.model, paused, (tool,), guarded, deps.traceparent,
                                                max_iterations=SEND_LOOP_CALLS, timeout=LLM_CALL_TIMEOUT_SECS))


async def _run(guarded: GuardedGateway, loop) -> SendResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("post failed at the gateway: %s", type(e).__name__)
        return _settled(guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if guarded.sent:  # the write went out; only the closing LLM call failed
            log.exception("post completed but the closing model call failed")
            return SendResult("sent")
        raise
    if result.status in _PAUSED and result.pending is not None:
        return _paused(result)
    return _settled(guarded, "the message was not posted")


def _paused(result: LoopResult) -> SendResult:
    try:
        hitl = approval_pause_metadata(result.pending.result)
    except NoHitlRequestError:
        log.warning("gateway asked for approval without a hitl request id")
        return SendResult("not_sent", "posting needs an approval the platform could not create")
    return SendResult("paused", loop=result.to_metadata(), hitl_request_id=hitl["hitl_request_id"])


def _settled(guarded: GuardedGateway, reason: str) -> SendResult:
    if guarded.sent:
        return SendResult("sent")
    denied = guarded.denied
    if denied is not None:
        return SendResult("denied", "denied on the approval card") if denied.by_user else SendResult("blocked")
    return SendResult("not_sent", reason)


def _send_request(post: Post, approval: Approval) -> str:
    return (f"Post with the tool {post.tool}.\nChannel or user id: {approval.recipient}\n"
            f"Message text:\n{prompts.fence_approved(approval.draft)}")
