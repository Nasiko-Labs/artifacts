"""A gateway adapter that records every tool result's raw text for this turn (`seen_text`), so a
download link is fetched only when it appears verbatim in tool output, and marks the result as untrusted
data. No model ever reads it in this agent; conversion and parsing happen in code."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult

_OPEN, _CLOSE = "<tool_output>", "</tool_output>"


def raw_text(result: ToolOk) -> str:
    """The gateway's own text: the text content items, else the structured content as JSON."""
    content = result.raw.get("content") if isinstance(result.raw, Mapping) else None
    if isinstance(content, list):
        texts = [str(i.get("text", "")) for i in content if isinstance(i, Mapping) and i.get("type") == "text"]
        if any(texts):
            return "\n".join(texts)
    structured = result.raw.get("structuredContent") if isinstance(result.raw, Mapping) else None
    if isinstance(structured, Mapping):
        return json.dumps(dict(structured), default=str)
    return result.text


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); records and wraps the text.

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
        body = raw_text(result)
        self._seen = (*self._seen, body)
        fenced = ({"type": "text", "text": f"{_OPEN}\n{body.replace(_CLOSE, ' ')}\n{_CLOSE}"},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
