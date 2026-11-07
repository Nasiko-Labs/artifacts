"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and remembers
each call's raw output: `seen_text` is what the verbatim checks compare against, `calls` is where the spec's
own text is taken from (by code, never echoed by the model)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import NasikoMcpClient, ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000
MAX_KEPT_CHARS = 120_000


@dataclass(frozen=True)
class SeenCall:
    name: str
    arguments: Mapping[str, Any]
    body: str
    is_error: bool


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per turn: `seen_text` is this turn's tool output only."""

    def __init__(self, gateway: NasikoMcpClient):
        self._gateway = gateway
        self._calls: tuple[SeenCall, ...] = ()

    @property
    def calls(self) -> tuple[SeenCall, ...]:
        return self._calls

    @property
    def seen_text(self) -> str:
        return "\n".join(c.body for c in self._calls)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        self._calls = (*self._calls, SeenCall(name, dict(arguments), body[:MAX_KEPT_CHARS], result.is_error))
        fenced = ({"type": "text", "text": fence(_cap(body))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
