"""(Copied in once; owned by this agent.) A gateway adapter for untrusted tool output: forwards `call_tool`
unchanged (same full name, arguments, traceparent), caps each result's text and remembers it, so every item
built from it can be traced back to what a tool returned. Tool output reaches the model only as evidence
items, inside prompts.fence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import NasikoMcpClient, ToolOk, ToolResult

MAX_TOOL_CHARS = 12_000


def original_text(result: ToolOk) -> str:
    """The gateway's own, uncapped text (the adapter rewrites `content` but keeps `raw`): parse this."""
    content = result.raw.get("content") if isinstance(result.raw, Mapping) else None
    if isinstance(content, list):
        texts = [str(i.get("text", "")) for i in content if isinstance(i, Mapping) and i.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return result.text or json.dumps(dict(result.raw), default=str)


def tool_text(result: ToolOk) -> str:
    body = original_text(result)
    return body if len(body) <= MAX_TOOL_CHARS else body[:MAX_TOOL_CHARS]


class FencedGateway:
    """One instance per turn: `seen_text` is this turn's tool output only."""

    def __init__(self, gateway: NasikoMcpClient):
        self._gateway = gateway
        self._seen: tuple[str, ...] = ()

    @property
    def seen_text(self) -> str:
        return "\n".join(self._seen)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = tool_text(result)
        self._seen = (*self._seen, body)
        return ToolOk(content=({"type": "text", "text": body},), is_error=result.is_error, raw=result.raw)
