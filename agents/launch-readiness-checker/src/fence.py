"""(Copied in once; owned by this agent.) A gateway adapter that fences every tool result as untrusted data
before the model sees it, and remembers the raw text so the post-check can confirm each id, title, link and
date came from a tool. It keeps which tool produced each body (so doc text is taken from the read calls only)
and whether the body was cut at the cap."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from config import DOC_TEXT_CHARS
from nasiko_mcp import NasikoMcpClient, ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = DOC_TEXT_CHARS


@dataclass(frozen=True)
class Record:
    tool: str
    body: str
    capped: bool


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per step: `records` is this step's tool output only.
    """

    def __init__(self, gateway: NasikoMcpClient):
        self._gateway = gateway
        self._records: tuple[Record, ...] = ()

    @property
    def records(self) -> tuple[Record, ...]:
        return self._records

    @property
    def seen_text(self) -> str:
        return "\n".join(r.body for r in self._records)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        full = result.text or json.dumps(dict(result.raw), default=str)
        capped = len(full) > MAX_FENCED_CHARS
        body = full[:MAX_FENCED_CHARS] if capped else full
        self._records = (*self._records, Record(name, body, capped))
        shown = body + ("\n[truncated]" if capped else "")
        return ToolOk(content=({"type": "text", "text": fence(shown)},), is_error=result.is_error, raw=result.raw)
