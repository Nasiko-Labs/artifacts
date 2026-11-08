"""A gateway adapter that records every raw tool result for the code checks and hands the model only a
fenced, capped copy. Adapted from the promise-tracker's FencedGateway.

One `Ledger` per turn holds every call (step, tool, arguments, raw text); each step gets its own
`RecordingGateway`, so code knows which step a result belongs to and what it was asked.
"""

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
    step: str
    tool: str
    arguments: Mapping[str, Any]
    text: str


class Ledger:
    def __init__(self) -> None:
        self._calls: tuple[Call, ...] = ()

    def add(self, call: Call) -> None:
        self._calls = (*self._calls, call)

    @property
    def calls(self) -> tuple[Call, ...]:
        return self._calls

    def of(self, *steps: str) -> tuple[Call, ...]:
        return tuple(c for c in self._calls if c.step in steps)


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class RecordingGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); records the raw result
    in the ledger and returns a fenced copy. Errors and pauses pass through unrecorded."""

    def __init__(self, inner: Any, ledger: Ledger, step: str):
        self._inner = inner
        self._ledger = ledger
        self._step = step

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._inner.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        raw = result.text or json.dumps(dict(result.raw), default=str)
        if not result.is_error and result.raw:
            self._ledger.add(Call(self._step, name, dict(arguments), raw))
        fenced = ({"type": "text", "text": fence(_cap(raw))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
