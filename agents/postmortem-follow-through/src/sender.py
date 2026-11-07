"""Make the writes of ONE approved action: a tool loop offered only the approved tools, every call through
the send guard. Whether a write happened is decided here from the gateway's results, never from the
model's reply. A gateway `ask` pauses with the approval and the guard's state kept."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import prompts
import send_guard
from actions import Action
from corpus import dm_channel_ids
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
)
from send_guard import Approval
from tool_loop import LoopResult, resume_tool_loop, run_tool_loop

log = logging.getLogger("postmortem.sender")

LOOP_CALLS = {"nudge": 3, "reopen": 4}
AUTHORIZED = "authorized"
_PAUSED = ("approval_pending", "authorization_required")


class GuardedGateway:
    """Forwards a call only when the guard allows it as one of the approved writes not yet made. A
    completed call (ok, error or denied) uses that write up; a pause keeps it for the identical re-call."""

    def __init__(self, gateway: Any, approval: Approval, state: Mapping[str, Any] | None = None):
        state = state or {}
        self._gateway = gateway
        self._approval = approval
        self.done: dict[str, str] = dict(state.get("done") or {})  # kind → ok / error / denied / blocked
        self.learned: dict[str, list[str]] = {k: list(v) for k, v in (state.get("learned") or {}).items()}

    def state(self) -> dict:
        return {"done": dict(self.done), "learned": {k: list(v) for k, v in self.learned.items()}}

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        decision = send_guard.check(name, dict(arguments), self._approval, frozenset(self.done), self.learned)
        if not decision.allowed:
            log.warning("send guard refused %s: %s", name, decision.reason.split(":")[0])
            return _refusal(decision.reason)
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            return result
        self.done[decision.kind] = _outcome(result)
        if decision.kind == "open_dm" and self.done[decision.kind] == "ok":
            self.learned["message"] = list(dm_channel_ids(result.text))
        return result


def _outcome(result) -> str:
    if isinstance(result, ToolDenied):
        return "denied" if result.by_user else "blocked"
    if isinstance(result, ToolOk) and not result.is_error:
        return "ok"
    return "error"


def _refusal(reason: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})


@dataclass(frozen=True)
class SendResult:
    status: str                       # nudged / reopened / denied / blocked / failed / paused
    done: dict = field(default_factory=dict)
    reason: str = ""
    loop: dict | None = None
    guard: dict | None = None
    hitl_request_id: str | None = None


async def send(deps: TurnDeps, action: Action, approval: Approval) -> SendResult:
    tools, failure = await _tools(deps, approval)
    if failure:
        return SendResult("failed", reason=failure)
    guarded = GuardedGateway(deps.gateway, approval)
    messages = ({"role": "system", "content": prompts.send_system()},
                {"role": "user", "content": request_text(action, approval)})
    loop = run_tool_loop(deps.llm, deps.model, messages, tools, guarded, deps.traceparent,
                         max_iterations=LOOP_CALLS[action.kind])
    return await _run(action, guarded, loop)


async def resume_send(deps: TurnDeps, action: Action, approval: Approval, loop: Mapping[str, Any],
                      guard: Mapping[str, Any], answer: str) -> SendResult:
    """After the platform's card: only `authorized` re-runs the identical call; anything else is a
    denial (fail closed) and nothing is called again."""
    guarded = GuardedGateway(deps.gateway, approval, guard)
    if answer.strip().casefold() != AUTHORIZED:
        guarded.done.setdefault(_paused_kind(approval, loop), "denied")
        guarded.done.setdefault(_main_kind(action), "denied")  # a denied DM-open means no message either
        return settle(action, guarded, "denied on the approval card")
    tools, failure = await _tools(deps, approval)
    if failure:
        return settle(action, guarded, failure)
    paused = LoopResult.from_metadata(loop)
    return await _run(action, guarded, resume_tool_loop(deps.llm, deps.model, paused, tools, guarded,
                                                        deps.traceparent, max_iterations=LOOP_CALLS[action.kind]))


def _paused_kind(approval: Approval, loop: Mapping[str, Any]) -> str:
    name = str(((loop or {}).get("pending") or {}).get("name") or "")
    return next((w.kind for w in approval.writes if w.tool.casefold() == name.casefold()), "message")


async def _tools(deps: TurnDeps, approval: Approval) -> tuple[tuple[McpTool, ...], str]:
    names = tuple(dict.fromkeys(w.tool for w in approval.writes))
    steps = ("nudge",) if approval.action == "nudge" else ("reopen",)
    catalog, failure = await discover(deps, steps, extra_queries=names)
    if failure is not None:
        return (), failure
    pool = {t.name: t for t in (*catalog.listed, *(t for v in catalog.found.values() for t in v))}
    tools = tuple(pool[n] for n in names if n in pool)
    if not any(w.required and w.tool in pool for w in approval.writes):
        return (), "the approved tool is no longer granted to this agent"
    return tools, ""


async def _run(action: Action, guarded: GuardedGateway, loop) -> SendResult:
    try:
        result = await loop
    except McpGatewayError as e:
        log.warning("write failed at the gateway: %s", type(e).__name__)
        return settle(action, guarded, f"the gateway refused the call ({type(e).__name__})")
    except Exception:
        if _main_kind(action) in guarded.done:  # the write went out; only the closing model call failed
            log.exception("write completed but the closing model call failed")
            return settle(action, guarded, "")
        raise
    if result.status in _PAUSED and result.pending is not None:
        try:
            hitl = approval_pause_metadata(result.pending.result)
        except NoHitlRequestError:
            log.warning("gateway asked for approval without a hitl request id")
            return settle(action, guarded, "it needs an approval the platform could not create")
        return SendResult("paused", dict(guarded.done), loop=result.to_metadata(), guard=guarded.state(),
                          hitl_request_id=hitl["hitl_request_id"])
    return settle(action, guarded, "the write was not made")


def _main_kind(action: Action) -> str:
    return "message" if action.kind == "nudge" else "transition"


def settle(action: Action, guarded: GuardedGateway, reason: str) -> SendResult:
    main = guarded.done.get(_main_kind(action))
    success = "nudged" if action.kind == "nudge" else "reopened"
    status = {"ok": success, "denied": "denied", "blocked": "blocked"}.get(main or "", "failed")
    return SendResult(status, dict(guarded.done), reason if status == "failed" else "")


def request_text(action: Action, approval: Approval) -> str:
    specs = {w.kind: w for w in approval.writes}
    if action.kind == "nudge":
        message, opener = specs["message"], specs.get("open_dm")
        first = (f"If {message.tool} needs a conversation id rather than a user id, first open a direct message "
                 f"with user {message.bound_ids[0]} using {opener.tool}, then post to the conversation it returns.\n"
                 if opener else "")
        return (f"Send ONE direct message to chat user {message.bound_ids[0]} with {message.tool}.\n{first}"
                f"Message:\n{prompts.fence_approved(message.body or '')}")
    transition = specs["transition"]
    lines = [f"On ticket {transition.bound_ids[0]}:",
             f"1. Call {transition.tool} with transition id {transition.bound_ids[1]}. Pass no text."]
    if "comment" in specs:
        lines.append(f"2. Call {specs['comment'].tool} to add this comment:\n"
                     f"{prompts.fence_approved(specs['comment'].body or '')}")
    return "\n".join(lines)
