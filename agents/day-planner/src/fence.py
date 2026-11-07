"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and keeps
the raw text: code reads the events from it, and the model's output is checked against it.
Adapted from the promise-tracker agent; owned here."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000
# What code parses events from; far above any single day's calendar, bounded against runaway output.
MAX_RAW_CHARS = 2_000_000


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per turn: `bodies` is this turn's successful tool output only.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._bodies: tuple[str, ...] = ()

    @property
    def bodies(self) -> tuple[str, ...]:
        return self._bodies

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        if not result.is_error:
            self._bodies = (*self._bodies, body[:MAX_RAW_CHARS])
        fenced = ({"type": "text", "text": fence(_cap(body))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
