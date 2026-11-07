"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and
records each call (name, arguments, full raw text) so code can verify ids, quotes and amounts against
what a tool really returned, and replay a call's argument shape (replay.py). One instance per turn."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model. The record keeps
# the full text: transcripts are checked against all of it.
MAX_FENCED_CHARS = 12_000


@dataclass(frozen=True)
class Recorded:
    name: str
    arguments: Mapping[str, Any]
    text: str
    ok: bool


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text."""

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._records: tuple[Recorded, ...] = ()

    @property
    def records(self) -> tuple[Recorded, ...]:
        return self._records

    @property
    def seen_text(self) -> str:
        return "\n".join(r.text for r in self._records)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        self._records = (*self._records, Recorded(name, dict(arguments), body, not result.is_error))
        shown = body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"
        return ToolOk(content=({"type": "text", "text": fence(shown)},), is_error=result.is_error, raw=result.raw)
