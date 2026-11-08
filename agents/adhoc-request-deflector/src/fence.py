"""The read side's gateway adapters (adapted from the MCP kit; owned here):

- `ReadOnly` refuses any write tool and the gateway's generic call meta-tool on the read path, so a read
  step can never change anything.
- `Recorder` keeps every read this turn (name, arguments, raw text), so `seen_text` holds exactly what the
  tools returned; tool output reaches the model only inside `prompts.fence`.
- `CallBudget` refuses reads past the turn's limit without reaching the gateway.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import send_guard
from nasiko_mcp import ToolDenied, ToolOk, ToolResult

log = logging.getLogger("deflector.fence")

CALL_META = "nasiko_call_tool"
READ_ONLY_REFUSAL = "Reads only here: write calls are refused."


@dataclass(frozen=True)
class CallRecord:
    name: str
    arguments: Mapping[str, Any]
    text: str
    is_error: bool


class ReadOnly:
    def __init__(self, gateway: Any):
        self._gateway = gateway

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if name == CALL_META or send_guard.is_write_tool(name):
            log.warning("refused a write call to %s on the read path", name)
            return ToolDenied(tool_name=name, message=READ_ONLY_REFUSAL, by_user=False)
        return await self._gateway.call_tool(name, arguments, traceparent)


class Recorder:
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
            self._calls = (*self._calls, CallRecord(name, dict(arguments), body, result.is_error or error_body(body)))
        return result


class CallBudget:
    def __init__(self, gateway: Any, limit: int):
        self._gateway = gateway
        self._limit = limit
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self._used >= self._limit:
            note = json.dumps({"error": "the read budget for this turn is used up"})
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
