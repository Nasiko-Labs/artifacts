"""A gateway adapter that records every raw tool result (with its arguments) for the code checks and hands
the model only a fenced, capped copy. Adapted from the promise-tracker's FencedGateway; owned here."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000


@dataclass(frozen=True)
class Call:
    tool: str
    arguments: Mapping[str, Any]
    text: str


class Ledger:
    """One per turn: every successful tool result, raw."""

    def __init__(self) -> None:
        self._calls: tuple[Call, ...] = ()

    def add(self, call: Call) -> None:
        self._calls = (*self._calls, call)

    @property
    def calls(self) -> tuple[Call, ...]:
        return self._calls


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class RecordingGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); records the raw result and
    returns a fenced copy. Errors and pauses pass through unrecorded."""

    def __init__(self, inner: Any, ledger: Ledger):
        self._inner = inner
        self._ledger = ledger

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._inner.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        raw = result.text or json.dumps(dict(result.raw), default=str)
        if not result.is_error:
            self._ledger.add(Call(name, dict(arguments), raw))
        return ToolOk(content=({"type": "text", "text": fence(_cap(raw))},), is_error=result.is_error, raw=result.raw)
