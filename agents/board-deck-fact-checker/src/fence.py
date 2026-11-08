"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and keeps
the raw text of each result: the deck text and the cell values are taken from it in code, and every
name, link and value in the report must appear in it."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult
from prompts import fence
from tool_health import failure_text

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000
MAX_KEPT_CHARS = 400_000  # per result, for the code that reads the deck and the cells


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.
    One instance per step: `results` is this step's tool output only."""

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._results: tuple[tuple[str, str], ...] = ()

    @property
    def results(self) -> tuple[tuple[str, str], ...]:
        """(tool name, raw text) per successful call, in call order."""
        return self._results

    @property
    def seen_text(self) -> str:
        return "\n".join(body for _, body in self._results)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        if failure_text(result) is None:
            self._results = (*self._results, (name, body[:MAX_KEPT_CHARS]))
        fenced = ({"type": "text", "text": fence(_cap(body))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
