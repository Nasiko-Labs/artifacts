"""A gateway adapter that fences every tool result as untrusted data before the model sees it,
and remembers the raw text so the post-check can confirm each event, quote, link and date came from a tool.

Copied from the promise-tracker agent and owned here. It wraps any ToolCaller (in this agent, the
turn's ToolHealth), so one meeting's `seen_text` holds only that meeting's tool output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult
from prep_prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per turn: `seen_text` is this turn's tool output only.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._seen: tuple[str, ...] = ()

    @property
    def seen_text(self) -> str:
        return "\n".join(self._seen)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = _cap(result.text or json.dumps(dict(result.raw), default=str))
        self._seen = (*self._seen, body)
        fenced = ({"type": "text", "text": fence(body)},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
