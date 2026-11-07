"""(Copied in once; owned by this agent.) A gateway adapter that fences every tool result as untrusted
data before the model sees it, and records each call (tool, arguments, raw text) so code can read the
results by shape and the post-check can confirm every command, path and link came from a tool."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000
# What code keeps of one result (files are cut again, at 24,000 chars, by records.py).
MAX_RECORDED_CHARS = 200_000


@dataclass(frozen=True)
class Call:
    """One forwarded tool call and what came back."""

    name: str
    arguments: Mapping[str, Any]
    text: str
    is_error: bool = False


def _cap(body: str, limit: int) -> str:
    return body if len(body) <= limit else body[:limit] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per turn: `calls` is this turn's tool output only.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._calls: tuple[Call, ...] = ()

    @property
    def calls(self) -> tuple[Call, ...]:
        return self._calls

    @property
    def seen_text(self) -> str:
        return "\n".join(c.text for c in self._calls)

    def mark(self) -> int:
        """A position in this turn's tool output; `since(mark)` is what came after it."""
        return len(self._calls)

    def since(self, mark: int) -> tuple[Call, ...]:
        return self._calls[mark:]

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        self._calls = (*self._calls, Call(name, dict(arguments), _cap(body, MAX_RECORDED_CHARS), result.is_error))
        fenced = ({"type": "text", "text": fence(_cap(body, MAX_FENCED_CHARS))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
