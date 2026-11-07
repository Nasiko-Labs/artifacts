"""(Copied in once from the MCP kit; owned by this agent.) A gateway adapter that fences every tool result
as untrusted data before the model sees it, and remembers what each call returned, uncut, so code can parse
the threads and the post-check can confirm every quote and link came from a tool."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_mcp import NasikoMcpClient, ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000


@dataclass(frozen=True)
class Recorded:
    """One successful tool call: what was called and the full text it returned."""

    name: str
    arguments: Mapping[str, Any]
    text: str


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per turn: `seen_text` and `records` are this turn's tool output only.
    """

    def __init__(self, gateway: NasikoMcpClient):
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
        if not result.is_error:
            self._records = (*self._records, Recorded(name, dict(arguments), body))
        fenced = ({"type": "text", "text": fence(_cap(body))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
