"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and
records each call (name, arguments, raw text, error flag) so code can attribute output to a channel
and the post-check can confirm every quote came from a tool. Adapted from the MCP kit; owned here."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import ToolDenied, ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000


@dataclass(frozen=True)
class CallRecord:
    name: str
    arguments: Mapping[str, Any]
    text: str
    is_error: bool


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per step: `seen_text` and `calls` are this step's tool output only.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._calls: tuple[CallRecord, ...] = ()

    @property
    def calls(self) -> tuple[CallRecord, ...]:
        return self._calls

    @property
    def seen_text(self) -> str:
        return "\n".join(c.text for c in self._calls if not c.is_error)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if isinstance(result, ToolDenied):
            self._calls = (*self._calls, CallRecord(name, dict(arguments), result.message, True))
            return result
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        failed = result.is_error or error_body(body)
        self._calls = (*self._calls, CallRecord(name, dict(arguments), body, failed))
        fenced = ({"type": "text", "text": fence(_cap(body))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)


class CallBudget:
    """Refuses calls past `limit` for this step without reaching the gateway."""

    def __init__(self, gateway: Any, limit: int):
        self._gateway = gateway
        self._limit = limit
        self._used = 0

    @property
    def exhausted(self) -> bool:
        return self._used >= self._limit

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self._used >= self._limit:
            note = json.dumps({"error": "the call budget for this step is used up; stop calling tools and answer"})
            return ToolOk(content=({"type": "text", "text": note},), is_error=True, raw={})
        self._used += 1
        return await self._gateway.call_tool(name, arguments, traceparent)


def error_body(text: str) -> bool:
    """True when a tool's JSON body says the call failed (`ok: false`, `error: ...`)."""
    try:
        body = json.loads(text)
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    return any(body.get(flag) is False for flag in ("ok", "successful", "success")) or bool(body.get("error"))
