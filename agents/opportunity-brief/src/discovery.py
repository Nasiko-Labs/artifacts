"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name (copied in from the hardened kit; owned by this agent).

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title, so the
code-host tools may be missing from it; the agent asks the search meta-tool itself, once per query,
concurrently, on every request (it needs this request's traceparent, so nothing is cached). No LLM call is
involved. The meta-tools are never called as tools. No provider or tool is named here. Only reads are kept:
anything whose name or description reads as a write is dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("opportunity_brief.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

# Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[str, ...] = (
    "search repositories by keyword and topic",
    "get repository details and readme",
)

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove",
    "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "join", "leave",
    "rename", "upload", "insert", "write", "publish", "schedule", "star", "unstar", "share", "submit",
    "import", "cancel", "complete", "close", "assign", "approve", "transfer", "notify", "fork", "merge",
    "commit", "push", "dispatch", "rerun", "lock", "unlock", "follow", "unfollow", "watch", "unwatch",
})
_TOKENS = re.compile(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])")


@dataclass(frozen=True)
class Catalog:
    tools: tuple[McpTool, ...]
    searched: bool = False


def looks_like_write(tool: McpTool) -> bool:
    """The name has a write verb, or the description opens with one ("Creates ...")."""
    tokens = {t.casefold() for t in _TOKENS.findall(tool.name)}
    if tokens & WRITE_VERBS:
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's read-only catalog, or the reason it is unavailable (a coverage word)."""
    if deps.gateway is None:
        return None, "missing"
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, "unreachable"
    names = {t.name for t in listed}
    found: tuple[McpTool, ...] = ()
    searched = False
    if SEARCH_META in names:
        outcomes = await asyncio.gather(*(_ask(deps, q) for q in DISCOVERY_QUERIES), return_exceptions=True)
        for query, outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
                continue
            searched = True
            found = (*found, *outcome)
    tools = tuple(t for t in _unique((*found, *listed)) if t.name not in META and not looks_like_write(t))
    log.info("read tools: %s", [t.name for t in tools])
    return (Catalog(tools, searched), None) if tools else (None, "missing")


async def _ask(deps: TurnDeps, query: str) -> tuple[McpTool, ...]:
    result = await deps.gateway.call_tool(SEARCH_META, {"query": query, "limit": SEARCH_LIMIT}, deps.traceparent)
    if not isinstance(result, ToolOk) or result.is_error:
        raise McpGatewayError(f"search returned {type(result).__name__}")
    structured = result.raw.get("structuredContent")
    payload: Any = structured if isinstance(structured, Mapping) else _json(result.text)
    tools = payload.get("tools") if isinstance(payload, Mapping) else None
    if not isinstance(tools, list):
        raise McpGatewayError("search result has no `tools` list")
    return tuple(t for t in (_tool(raw) for raw in tools) if t is not None)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _tool(raw: Any) -> McpTool | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str) or not raw["name"]:
        return None
    schema = raw.get("inputSchema")
    return McpTool(name=raw["name"], description=str(raw.get("description") or ""),
                   input_schema=MappingProxyType(dict(schema) if isinstance(schema, Mapping) else {}))


def _unique(tools: Iterable[McpTool]) -> tuple[McpTool, ...]:
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
