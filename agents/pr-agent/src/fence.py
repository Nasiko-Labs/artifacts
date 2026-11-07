"""A gateway adapter that fences every tool result as untrusted data before the model sees it, and
keeps the full raw text so code can read the PR's files and metadata out of it.

The model only needs to see enough to steer (which tool worked, whether a page was full); the
diff it analyzes is rebuilt in code from `raw_texts`, never from the model's restatement.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import NasikoMcpClient, ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per turn: `raw_texts` is this turn's tool output only, uncapped.
    """

    def __init__(self, gateway: NasikoMcpClient):
        self._gateway = gateway
        self._raw: tuple[str, ...] = ()

    @property
    def raw_texts(self) -> tuple[str, ...]:
        return self._raw

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        body = result.text or json.dumps(dict(result.raw), default=str)
        if not result.is_error:
            self._raw = (*self._raw, body)
        fenced = ({"type": "text", "text": fence(_cap(body))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
