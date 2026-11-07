"""Framework-neutral OpenAI function-calling loop over the Nasiko MCP gateway (demo-agents kit).

    tools = await mcp.list_tools(traceparent)
    if not tools: ...refuse...
    result = await run_tool_loop(llm, model, messages, tools, mcp, traceparent)

`llm` is any OpenAI-compatible async client (`llm.chat.completions.create`). Every LLM call
carries `extra_headers={"traceparent": ...}` inside the inbound OTel context, so the LLM router
can attribute it. The model sees the listed tools unchanged and each call is forwarded by the
full listed name. The loop never names a provider or a tool.

The loop STOPS on `ApprovalPending` / `AuthorizationRequired`: the caller turns
`result.pending` into an A2A `input-required` pause (nasiko_hitl.approval_pause_metadata),
stores `result.to_metadata()` on the task, and on resume calls `resume_tool_loop`, which
re-calls the same tool by the same full name and continues.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    GatewayAuthError,
    GatewayForbiddenError,
    McpGatewayError,
    McpTool,
    MissingTraceparentError,
    ToolDenied,
    ToolOk,
    ToolResult,
    openai_tools,
)
from nasiko_trace import bound_traceparent

log = logging.getLogger("nasiko_tool_loop")

DEFAULT_MAX_ITERATIONS = 8
DEFAULT_MAX_RESULT_CHARS = 16_000
OUT_OF_STEPS_TEXT = "I ran out of steps before finishing. Please narrow the request and try again."
_SKIPPED = json.dumps({"status": "skipped", "reason": "an earlier call in this turn paused for a human"})
# Raised, not fed back: the flow or credential is broken and the model cannot fix it.
_FATAL_ERRORS = (GatewayAuthError, GatewayForbiddenError, MissingTraceparentError)

Status = Literal["completed", "approval_pending", "authorization_required", "max_iterations"]


class ToolCaller(Protocol):
    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult: ...


@dataclass(frozen=True)
class PendingCall:
    """The tool call that paused the loop; re-call it unchanged after the human acts."""

    tool_call_id: str
    name: str
    arguments: Mapping[str, Any]
    result: ApprovalPending | AuthorizationRequired


@dataclass(frozen=True)
class LoopResult:
    status: Status
    text: str
    messages: tuple[dict, ...]
    pending: PendingCall | None = None
    tools_called: tuple[str, ...] = field(default=())

    def to_metadata(self) -> dict:
        """JSON-safe form to store on the A2A task across a pause."""
        return {
            "status": self.status,
            "text": self.text,
            "messages": list(self.messages),
            "pending": _pending_to_dict(self.pending) if self.pending else None,
            "tools_called": list(self.tools_called),
        }

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any]) -> LoopResult:
        pending = metadata.get("pending")
        return LoopResult(
            status=metadata["status"],
            text=str(metadata.get("text") or ""),
            messages=tuple(metadata.get("messages") or ()),
            pending=_pending_from_dict(pending) if pending else None,
            tools_called=tuple(metadata.get("tools_called") or ()),
        )


async def run_tool_loop(
    llm: Any,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[McpTool],
    gateway: ToolCaller,
    traceparent: str,
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    **completion_kwargs: Any,
) -> LoopResult:
    """Run model → tool calls → model until a final answer, a pause, or `max_iterations`
    LLM calls. `messages` is not modified."""
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")
    if not traceparent:
        raise MissingTraceparentError("the tool loop requires the inbound traceparent")
    catalog = {tool.name: tool for tool in tools}
    schemas = openai_tools(tools)
    history = tuple(dict(message) for message in messages)
    called: tuple[str, ...] = ()
    for _ in range(max_iterations):
        assistant = await _complete(llm, model, history, schemas, traceparent, completion_kwargs)
        history = (*history, assistant)
        calls = assistant.get("tool_calls") or []
        if not calls:
            return LoopResult("completed", str(assistant.get("content") or ""), history, None, called)
        history, pending, called = await _run_batch(
            gateway, catalog, calls, history, called, traceparent, max_result_chars
        )
        if pending is not None:
            return LoopResult(_pause_status(pending), pending.result.message, history, pending, called)
    log.warning("tool loop stopped after %d LLM calls", max_iterations)
    return LoopResult("max_iterations", OUT_OF_STEPS_TEXT, history, None, called)


async def resume_tool_loop(
    llm: Any,
    model: str,
    paused: LoopResult,
    tools: Sequence[McpTool],
    gateway: ToolCaller,
    traceparent: str,
    **loop_kwargs: Any,
) -> LoopResult:
    """After the human resolved the pause: re-call the paused tool (same full name, same
    arguments), then continue the loop. If the gateway asks again, this pauses again."""
    pending = paused.pending
    if pending is None:
        raise ValueError("resume_tool_loop needs a paused LoopResult")
    max_chars = loop_kwargs.get("max_result_chars", DEFAULT_MAX_RESULT_CHARS)
    outcome = await _forward(gateway, pending.name, pending.arguments, traceparent)
    if isinstance(outcome, (ApprovalPending, AuthorizationRequired)):
        again = replace(pending, result=outcome)
        return LoopResult(_pause_status(again), outcome.message, paused.messages, again, paused.tools_called)
    called = (*paused.tools_called, pending.name)
    history = (*paused.messages, _tool_message(pending.tool_call_id, _render(outcome, max_chars)))
    result = await run_tool_loop(llm, model, history, tools, gateway, traceparent, **loop_kwargs)
    return replace(result, tools_called=(*called, *result.tools_called))


async def _complete(
    llm: Any, model: str, history: tuple[dict, ...], schemas: list[dict], traceparent: str,
    completion_kwargs: Mapping[str, Any],
) -> dict:
    request: dict[str, Any] = {"model": model, "messages": list(history), **completion_kwargs}
    if schemas:  # OpenAI rejects an empty `tools` list
        request["tools"] = schemas
    headers = {**dict(completion_kwargs.get("extra_headers") or {}), "traceparent": traceparent}
    request["extra_headers"] = headers
    with bound_traceparent(traceparent):
        response = await llm.chat.completions.create(**request)
    message = response.choices[0].message
    dumped = message.model_dump(exclude_none=True) if hasattr(message, "model_dump") else dict(message)
    # Some OpenAI-compatible backends require `content` even on a tool-call-only message.
    return {"content": "", **dumped} if dumped.get("content") is None else dumped


async def _run_batch(
    gateway: ToolCaller,
    catalog: Mapping[str, McpTool],
    calls: list[dict],
    history: tuple[dict, ...],
    called: tuple[str, ...],
    traceparent: str,
    max_chars: int,
) -> tuple[tuple[dict, ...], PendingCall | None, tuple[str, ...]]:
    pending: PendingCall | None = None
    for call in calls:
        call_id = str(call.get("id") or "")
        if pending is not None:  # every tool_call needs a reply before the next LLM call
            history = (*history, _tool_message(call_id, _SKIPPED))
            continue
        name, outcome = await _handle_call(gateway, catalog, call, traceparent)
        if isinstance(outcome, (ApprovalPending, AuthorizationRequired)):
            pending = PendingCall(call_id, name, _arguments(call) or {}, outcome)
            continue
        called = (*called, name) if name in catalog else called
        history = (*history, _tool_message(call_id, _render(outcome, max_chars)))
    return history, pending, called


async def _handle_call(
    gateway: ToolCaller, catalog: Mapping[str, McpTool], call: dict, traceparent: str
) -> tuple[str, ToolResult | str]:
    name = str(call.get("function", {}).get("name") or "")
    arguments = _arguments(call)
    if arguments is None:
        return name, _error("arguments must be a JSON object")
    if name not in catalog:
        log.info("model named a tool not in the catalog: %r", name)
        return name, _error(f"unknown tool {name!r}: it is not in your tool list; use only listed tools")
    return name, await _forward(gateway, name, arguments, traceparent)


async def _forward(
    gateway: ToolCaller, name: str, arguments: Mapping[str, Any], traceparent: str
) -> ToolResult | str:
    try:
        return await gateway.call_tool(name, arguments, traceparent)
    except _FATAL_ERRORS:
        raise
    except McpGatewayError as e:
        log.warning("tool %s failed at the gateway: %s", name, type(e).__name__)
        return _error(f"{type(e).__name__}: {e}")


def _arguments(call: dict) -> dict | None:
    raw = call.get("function", {}).get("arguments") or "{}"
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _render(outcome: ToolResult | str, max_chars: int) -> str:
    if isinstance(outcome, str):
        return outcome
    if isinstance(outcome, ToolDenied):
        return _error(outcome.message)
    if isinstance(outcome, ToolOk):
        body = outcome.text or json.dumps(dict(outcome.raw), default=str)
        return _truncate(f"Tool error: {body}" if outcome.is_error else body, max_chars)
    return _error(outcome.message)  # pause results never reach here; kept total for safety


def _truncate(body: str, max_chars: int) -> str:
    if len(body) <= max_chars:
        return body
    return body[:max_chars] + f"\n…[truncated {len(body) - max_chars} chars]"


def _tool_message(tool_call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _error(message: str) -> str:
    return json.dumps({"error": message})


def _pause_status(pending: PendingCall) -> Status:
    return "approval_pending" if isinstance(pending.result, ApprovalPending) else "authorization_required"


def _pending_to_dict(pending: PendingCall) -> dict:
    result = pending.result
    if isinstance(result, ApprovalPending):
        encoded = {"kind": "approval_pending", "tool_name": result.tool_name, "message": result.message,
                   "hitl_request_ids": list(result.hitl_request_ids), "server": result.server}
    else:
        encoded = {"kind": "authorization_required", "tool_name": result.tool_name, "message": result.message,
                   "hitl_request_id": result.hitl_request_id, "connector": result.connector}
    return {"tool_call_id": pending.tool_call_id, "name": pending.name,
            "arguments": dict(pending.arguments), "result": encoded}


def _pending_from_dict(data: Mapping[str, Any]) -> PendingCall:
    raw = data["result"]
    result: ApprovalPending | AuthorizationRequired
    if raw["kind"] == "approval_pending":
        result = ApprovalPending(
            raw["tool_name"], raw.get("message") or "", tuple(raw.get("hitl_request_ids") or ()), raw.get("server")
        )
    else:
        result = AuthorizationRequired(
            raw["tool_name"], raw.get("message") or "", raw.get("hitl_request_id"), raw.get("connector")
        )
    return PendingCall(data["tool_call_id"], data["name"], dict(data.get("arguments") or {}), result)
