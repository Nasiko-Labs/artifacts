"""Chat is counted and located, not read: a gateway adapter for the chat step that removes message
text from every tool result before the model (or the fence record) sees it. Only structure survives:
ids, timestamps, permalinks, channel names, counts. A result that is not JSON is withheld whole,
because its text cannot be separated from the message bodies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import ToolOk, ToolResult

# Keys whose values carry what people wrote; dropped wherever they appear.
TEXT_KEYS = frozenset({"text", "body", "content", "blocks", "attachments", "files", "snippet", "preview",
                       "rich_text", "markdown", "summary", "title", "description", "topic", "purpose", "comment",
                       "previous_message", "root", "reactions"})
WITHHELD = json.dumps({"note": "non-JSON chat result withheld: message text is never read"})


def strip_text(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: strip_text(v) for k, v in value.items()
                if k not in TEXT_KEYS and not (k == "message" and isinstance(v, str))}
    if isinstance(value, list):
        return [strip_text(v) for v in value]
    return value


def redact_text(text: str) -> str:
    try:
        body = json.loads(text)
    except ValueError:
        return WITHHELD
    return json.dumps(strip_text(body), default=str)


class RedactingGateway:
    def __init__(self, gateway: Any):
        self._gateway = gateway

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        source = result.text or json.dumps(dict(result.raw.get("structuredContent") or {}), default=str)
        content = ({"type": "text", "text": redact_text(source)},)
        return ToolOk(content=content, is_error=result.is_error, raw={"content": list(content),
                                                                       "isError": result.is_error})
