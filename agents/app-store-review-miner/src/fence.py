"""A gateway adapter that fences every tool result as untrusted data before the model sees it, records
each result's full text (so code can verify ids and review text against what a tool really returned), and
refuses calls past the turn's tool-call cap without reaching the gateway. One instance per turn."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000
CAP_NOTE = json.dumps({"error": "Tool-call limit for this turn reached. Stop calling tools and answer."})


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text."""

    def __init__(self, gateway: Any, max_calls: int):
        self._gateway = gateway
        self._max_calls = max_calls
        self._calls = 0
        self._texts: tuple[str, ...] = ()

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def seen_text(self) -> str:
        return "\n".join(self._texts)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if self._calls >= self._max_calls:
            return ToolOk(content=({"type": "text", "text": CAP_NOTE},), is_error=True, raw={})
        self._calls += 1
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        self._texts = (*self._texts, body)
        shown = body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"
        return ToolOk(content=({"type": "text", "text": fence(shown)},), is_error=result.is_error, raw=result.raw)
