"""Records every chat read this turn (name, arguments, raw text, error flag), so code can attribute
output to a query or a thread and the post-check can confirm every quote, link and id came from a
tool (`seen_text`). Tool output reaches the model only inside `prompts.fence`. Adapted from the MCP kit."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import ToolDenied, ToolOk, ToolResult


@dataclass(frozen=True)
class CallRecord:
    name: str
    arguments: Mapping[str, Any]
    text: str
    is_error: bool


class Recorder:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent) and keeps a record."""

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
        elif isinstance(result, ToolOk):
            body = result.text or json.dumps(dict(result.raw), default=str)
            failed = result.is_error or error_body(body)
            self._calls = (*self._calls, CallRecord(name, dict(arguments), body, failed))
        return result


class CallBudget:
    """Refuses calls past `limit` for this turn without reaching the gateway."""

    def __init__(self, gateway: Any, limit: int):
        self._gateway = gateway
        self._limit = limit
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    @property
    def left(self) -> int:
        return self._limit - self._used

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self._used >= self._limit:
            note = json.dumps({"error": "the call budget for this turn is used up"})
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
