"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and
records each call's text so code can check every value the model cites (and read some by shape).
Copied from promise-tracker and owned here; it now keeps one Record per call."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from corpus import Corpus, Record
from nasiko_mcp import ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per step: `records` is this step's successful tool output only, capped exactly as
    the model saw it, so nothing the model could not see is ever accepted as backing.
    """

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._records: tuple[Record, ...] = ()

    @property
    def records(self) -> tuple[Record, ...]:
        return self._records

    def corpus(self) -> Corpus:
        return Corpus(self._records)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = _cap(result.text or json.dumps(dict(result.raw), default=str))
        if not result.is_error:
            self._records = (*self._records, Record(name, dict(arguments), body))
        fenced = ({"type": "text", "text": fence(body)},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
