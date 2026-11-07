"""A gateway adapter that fences every tool result as untrusted data before the model sees it, caps each call at
TOOL_CALL_TIMEOUT_SECS, and remembers the raw text so the post-check can confirm each repo, change, title, link,
login, user and channel id came from a tool this turn."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

from deps import TOOL_CALL_TIMEOUT_SECS
from nasiko_mcp import GatewayUnavailableError, ToolOk, ToolResult
from prompts import fence

# Below the kit loop's 16k truncation, so the closing fence always reaches the model.
MAX_FENCED_CHARS = 12_000


def _cap(body: str) -> str:
    return body if len(body) <= MAX_FENCED_CHARS else body[:MAX_FENCED_CHARS] + "\n[truncated]"


class FencedGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); wraps the text.

    One instance per turn: `seen_text` is this turn's tool output only.
    """

    def __init__(self, gateway: Any, timeout_secs: float = TOOL_CALL_TIMEOUT_SECS):
        self._gateway = gateway
        self._timeout = timeout_secs
        self._seen: tuple[str, ...] = ()

    @property
    def seen_text(self) -> str:
        return "\n".join(self._seen)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        try:
            result = await asyncio.wait_for(self._gateway.call_tool(name, arguments, traceparent), self._timeout)
        except TimeoutError as e:
            raise GatewayUnavailableError(f"{name} timed out after {self._timeout:.0f}s") from e
        if not isinstance(result, ToolOk):
            return result
        body = _cap(result.text or json.dumps(dict(result.raw), default=str))
        self._seen = (*self._seen, body)
        fenced = ({"type": "text", "text": fence(body)},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
