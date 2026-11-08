"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title, so the desk
tools this agent needs may be missing from it. The agent asks the search meta-tool itself, once per query,
concurrently, on every request (the gateway needs this request's traceparent, so nothing is cached). No LLM
call is involved. With tool search off the meta-tool is not listed and `tools/list` alone is used.

Read-only agent: anything that reads as a write is never offered. The model never sees the meta-tools.
No provider or tool is named here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import live_prompts
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("macro_gap_finder_live.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
PLATFORM_PREFIX = "nasiko_"  # the platform's own tools (memory search ...) are never a support desk
SEARCH_LIMIT = 8

Purpose = Literal["tickets", "macros"]

# (purpose, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Purpose, str], ...] = (
    ("tickets", "search solved support tickets within a date range"),
    ("tickets", "get a support ticket with all its comments"),
    ("tickets", "list comments of a ticket with author and visibility"),
    ("macros", "list support macros or saved replies with their text"),
)

# Name tokens and description verbs that mark a tool as a write. A read-only agent never offers one.
WRITE_WORDS = frozenset({
    "create", "update", "delete", "remove", "send", "post", "reply", "add", "edit", "set", "assign", "close",
    "merge", "apply", "modify", "upload", "write", "put", "patch", "publish", "archive", "restore", "mark", "tag",
    "insert", "move", "invite", "execute", "submit", "solve", "reopen", "make", "trash", "unassign", "bulk",
    "save", "share", "keep", "rename", "copy", "grant", "revoke", "enable", "disable", "pin", "approve", "reject",
    "cancel", "start", "stop", "run", "trigger", "begin", "finish", "store", "forward", "notify", "schedule",
})
_TOKEN = re.compile(r"[a-z]+")
# Capability words a step's tools must show before a model call is spent on them (files or memory tools are not a
# desk): (words for the tool's name, words for its description). "conversation" counts only in a name, since file
# and chat tools describe themselves as acting "in this conversation".
STEP_WORDS: Mapping[str, tuple[re.Pattern, re.Pattern]] = MappingProxyType({
    "tickets": (re.compile(r"\b(?:tickets?|conversations?|help ?desk|requesters?)\b"),
                re.compile(r"\b(?:tickets?|help ?desk|support desk|customer support)\b")),
    "macros": (re.compile(r"\b(?:macros?|saved repl(?:y|ies)|canned|snippets?)\b"),
               re.compile(r"\b(?:macros?|saved repl(?:y|ies)|canned respon\w*)\b")),
})


@dataclass(frozen=True)
class Catalog:
    """This request's tools: `listed` is `tools/list` minus meta-tools; `found` is what each purpose's
    searches returned; `searched` is False when there was no search meta-tool or every query failed."""

    listed: tuple[McpTool, ...]
    found: Mapping[str, tuple[McpTool, ...]] = field(default_factory=dict)
    searched: bool = False
    recover: McpTool | None = None

    @property
    def tools(self) -> tuple[McpTool, ...]:
        return _unique((*self.listed, *self.found.get("tickets", ()), *self.found.get("macros", ())))

    def tools_for(self, purpose: Purpose) -> tuple[McpTool, ...]:
        """What one step may use: its search results (or, without any, the listed tools), minus writes."""
        pool = self.found.get(purpose) or self.listed
        reads = tuple(t for t in _unique(pool) if not looks_like_write(t))
        return (*reads, self.recover) if reads and self.recover else reads


def fits(tools: Iterable[McpTool], purpose: Purpose) -> bool:
    """Some tool's name or description shows the step's capability words."""
    in_name, in_description = STEP_WORDS[purpose]
    return any(in_name.search(t.name.casefold().replace("_", " ")) or in_description.search(t.description.casefold())
               for t in tools)


def looks_like_write(tool: McpTool) -> bool:
    """A name token reads as a write, or the description opens with a write verb ("Creates ...")."""
    tokens = set(_TOKEN.findall(tool.name.casefold().split("__")[-1]))
    if tokens & WRITE_WORDS:
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_WORDS)


async def discover(deps: Any) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, live_prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, live_prompts.NO_DESK_PASTE_HINT
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, live_prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if not t.name.startswith(PLATFORM_PREFIX) and t.name != RECOVER_META)
    catalog = Catalog(listed=real, recover=recover)
    if SEARCH_META in names:
        catalog = await _search(deps, catalog)
    if not fits(catalog.tools_for("tickets"), "tickets"):
        log.info("no offered tool looks like a support desk: %s", [t.name for t in catalog.tools_for("tickets")])
        return None, live_prompts.NO_DESK_PASTE_HINT
    log.info("desk-like: %s; tools listed: %s; found: %s",
             [t.name for t in catalog.tools_for("tickets") if fits((t,), "tickets")], [t.name for t in catalog.listed],
             {k: [t.name for t in v] for k, v in catalog.found.items()})
    return catalog, None


async def _search(deps: Any, catalog: Catalog) -> Catalog:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: dict[str, tuple[McpTool, ...]] = {}
    answered = 0
    for (purpose, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found = {**found, purpose: _unique((*found.get(purpose, ()), *outcome))}
    if not answered:
        log.warning("every tool search failed; falling back to tools/list alone")
        return catalog
    return Catalog(listed=catalog.listed, found=MappingProxyType(found), searched=True, recover=catalog.recover)


async def _ask(deps: Any, query: str) -> tuple[McpTool, ...]:
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
    if raw["name"].startswith(PLATFORM_PREFIX) or raw["name"] == RECOVER_META:
        return None
    schema = raw.get("inputSchema")
    return McpTool(name=raw["name"], description=str(raw.get("description") or ""),
                   input_schema=MappingProxyType(dict(schema) if isinstance(schema, Mapping) else {}))


def _unique(tools: Iterable[McpTool]) -> tuple[McpTool, ...]:
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
