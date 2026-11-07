"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name. Adapted from the meeting-prep agent; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title, so the
calendar reads may be missing from it. The agent asks the search meta-tool itself, once per query,
concurrently (the gateway needs this request's traceparent, so nothing is cached). No LLM call is
involved. With tool search off the meta-tool is not listed and `tools/list` alone is used.

Read-only: anything that reads as a write is never offered to the model, nor are the meta-tools. Only
tools that read as calendar tools are kept. No provider or tool is named here.
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

log = logging.getLogger("day_planner.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})  # never offered to the model
SEARCH_LIMIT = 8
MAX_OFFERED = 10

# Provider-neutral capability phrases, all reads; tune here.
DISCOVERY_QUERIES: tuple[str, ...] = (
    "list calendar events in a time range",
    "get a calendar event with its attendees and description",
    "list the calendars I have",
    "get my calendar settings and timezone",
    "get my own calendar profile",
)
_CALENDAR_WORDS = ("calendar", "event", "meeting", "schedule", "agenda", "free/busy", "freebusy", "timezone",
                   "time zone")

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove",
    "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "kick", "join",
    "leave", "rename", "upload", "insert", "write", "publish", "schedule", "star", "unstar", "share",
    "submit", "import", "cancel", "complete", "close", "assign", "approve", "transfer", "notify", "accept",
    "decline", "respond", "rsvp", "quick", "clear", "duplicate", "copy", "transition", "comment", "draft",
    "book", "watch", "subscribe",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """This request's calendar read tools (meta-tools removed), and `recover_compressed` if listed."""

    tools: tuple[McpTool, ...]
    recover: McpTool | None = None

    def offered(self) -> tuple[McpTool, ...]:
        reads = self.tools[:MAX_OFFERED]
        return (*reads, self.recover) if reads and self.recover else reads


def name_tokens(tool_name: str) -> tuple[str, ...]:
    """`c_1a2b__CAL_createEvent` → ("c", "1a2b", "cal", "create", "event")."""
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def looks_like_write(tool: McpTool) -> bool:
    """The name holds a write verb, or the description opens with one ("Creates ...")."""
    if any(token in WRITE_VERBS for token in name_tokens(tool.name)):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


def is_calendar(tool: McpTool) -> bool:
    text = f"{tool.name.casefold().replace('_', ' ')} {tool.description.casefold()}"
    return any(word in text for word in _CALENDAR_WORDS)


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's calendar catalog (possibly empty), or a coverage line when the gateway is missing
    or unreachable. The traceparent is checked by the caller."""
    if deps.gateway is None:
        return None, "missing"
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, "unreachable"
    names = {t.name for t in listed}
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if t.name not in HIDDEN_META and t.name != RECOVER_META)
    found = await _search(deps) if SEARCH_META in names else ()
    if SEARCH_META not in names:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    tools = tuple(t for t in _unique((*found, *real)) if is_calendar(t) and not looks_like_write(t))
    log.info("calendar tools: %s", [t.name for t in tools])
    return Catalog(tools, recover), None


async def _search(deps: TurnDeps) -> tuple[McpTool, ...]:
    outcomes = await asyncio.gather(*(_ask(deps, q) for q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    for query, outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        found = (*found, *outcome)
    return found


async def _ask(deps: TurnDeps, query: str) -> tuple[McpTool, ...]:
    result = await deps.gateway.call_tool(SEARCH_META, {"query": query, "limit": SEARCH_LIMIT}, deps.traceparent)
    if not isinstance(result, ToolOk) or result.is_error:
        raise McpGatewayError(f"search returned {type(result).__name__}")
    return tuple(t for t in (_tool(raw) for raw in _found(result)) if t is not None)


def _found(result: ToolOk) -> list:
    structured = result.raw.get("structuredContent")
    payload: Any = structured if isinstance(structured, Mapping) else _json(result.text)
    tools = payload.get("tools") if isinstance(payload, Mapping) else None
    if not isinstance(tools, list):
        raise McpGatewayError("search result has no `tools` list")
    return tools


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _tool(raw: Any) -> McpTool | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str) or not raw["name"]:
        return None
    if raw["name"] in HIDDEN_META or raw["name"] == RECOVER_META:
        return None
    schema = raw.get("inputSchema")
    return McpTool(name=raw["name"], description=str(raw.get("description") or ""),
                   input_schema=MappingProxyType(dict(schema) if isinstance(schema, Mapping) else {}))


def _unique(tools: Iterable[McpTool]) -> tuple[McpTool, ...]:
    """First occurrence of each name wins, in order."""
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
