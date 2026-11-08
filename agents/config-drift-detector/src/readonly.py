"""Read-only, enforced in code.

`looks_like_write(tool)` keeps write tools out of every catalog the model sees (name read as words,
or a description that opens with a write verb). `ReadOnlyGateway` sits directly on the gateway client
and refuses any call to a write tool, or to the gateway's generic call meta-tool (which could reach
one), even if a write somehow got named: nothing this agent does can change anything anywhere.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import McpTool, ToolDenied, ToolResult

log = logging.getLogger("drift.readonly")

CALL_META = "nasiko_call_tool"
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove",
    "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "kick", "join",
    "leave", "rename", "upload", "insert", "write", "publish", "schedule", "star", "unstar", "share",
    "submit", "import", "cancel", "complete", "close", "assign", "approve", "transfer", "notify", "merge",
    "transition", "dismiss", "lock", "unlock", "reopen", "unwatch", "save", "keep", "store", "put", "push",
    "overwrite", "append", "revert", "apply", "rollback", "restore",
})
# Not listed on purpose: "request", "comment", "label" also name what read tools return
# (`list_pull_request_reviews`, `get_comment`); a write tool carries a verb besides them.
REFUSAL = "This agent is read-only: write calls are refused."
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_name(tool_name: str) -> bool:
    tokens = name_tokens(tool_name)
    return any(t in WRITE_VERBS for t in tokens) or {"chat", "message"} <= set(tokens)


def looks_like_write(tool: McpTool) -> bool:
    if is_write_name(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


class ReadOnlyGateway:
    """Forwards reads unchanged; refuses writes and the call meta-tool without reaching the gateway."""

    def __init__(self, gateway: Any, writes: frozenset[str] = frozenset()):
        self._gateway = gateway
        self._writes = writes

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if name == CALL_META or name in self._writes or is_write_name(name):
            log.warning("refused a write call to %s", name)
            return ToolDenied(tool_name=name, message=REFUSAL, by_user=False)
        return await self._gateway.call_tool(name, arguments, traceparent)
