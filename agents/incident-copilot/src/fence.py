"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and keeps
the raw output in memory (never sent to the model) so code can read data by shape and check every id,
link, timestamp and quote the model cites against what a tool really returned."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import NasikoMcpClient, ToolOk, ToolResult
from prompts import fence

MAX_RAW_CHARS = 400_000     # per loop, in memory only
MAX_FENCED_CHARS = 12_000   # below the kit loop's 16k truncation, so the closing fence reaches the model


@dataclass(frozen=True)
class RawCall:
    """One successful tool call: the name, the arguments the agent forwarded, the raw text returned."""

    name: str
    arguments: Mapping[str, Any]
    text: str


def _cap(body: str, limit: int) -> str:
    return body if len(body) <= limit else body[:limit] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent) and rewrites the text the
    model sees: `shape(raw)` (default: a plain cap), fenced. One instance per loop."""

    def __init__(self, gateway: NasikoMcpClient, shape: Callable[[str], str] | None = None):
        self._gateway = gateway
        self._shape = shape or (lambda body: _cap(body, MAX_FENCED_CHARS))
        self._calls: tuple[RawCall, ...] = ()
        self._size = 0

    @property
    def calls(self) -> tuple[RawCall, ...]:
        return self._calls

    @property
    def seen_text(self) -> str:
        return "\n".join(call.text for call in self._calls)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        self._remember(name, arguments, body)
        fenced = ({"type": "text", "text": fence(self._shape(body))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)

    def _remember(self, name: str, arguments: Mapping[str, Any], body: str) -> None:
        room = MAX_RAW_CHARS - self._size
        if room <= 0:
            return
        kept = body[:room]
        self._size += len(kept)
        self._calls = (*self._calls, RawCall(name, dict(arguments), kept))
