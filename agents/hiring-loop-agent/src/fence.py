"""A gateway adapter that redacts compensation lines, fences every tool result as untrusted data before the
model sees it, and remembers the (redacted) text so every quote, name and link the model returns can be
checked against what a tool actually returned."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from checks import redact
from nasiko_mcp import ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000
CUT_NOTE = "\n[cut at 12,000 characters]"


def _cap(body: str) -> tuple[str, bool]:
    return (body, False) if len(body) <= MAX_FENCED_CHARS else (body[:MAX_FENCED_CHARS] + CUT_NOTE, True)


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per step: `seen_text` is this step's tool output only.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._seen: tuple[str, ...] = ()
        self._cut = 0
        self._redacted = 0

    @property
    def seen_text(self) -> str:
        return "\n".join(self._seen)

    @property
    def cut(self) -> int:
        return self._cut

    @property
    def redacted(self) -> int:
        return self._redacted

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        clean, removed = redact(result.text or json.dumps(dict(result.raw), default=str))
        body, was_cut = _cap(clean)
        self._seen = (*self._seen, body)
        self._cut += int(was_cut)
        self._redacted += removed
        fenced = ({"type": "text", "text": fence(body)},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
