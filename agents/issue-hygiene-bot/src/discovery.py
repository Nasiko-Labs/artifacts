"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked this purpose's fixed
capability queries (concurrently, this request's traceparent, no LLM, no cache), merged by name. Without the
meta-tool (search off) or when every search fails, `tools/list` alone is used.

The model never sees the meta-tools: found tools are called directly by their real names, so the send guard
always sees the real call. No provider or tool is named here. (Adapted from the hardened kit; owned here.)"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import replies
import send_guard
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("issue_hygiene.discovery")

SEARCH_META, CALL_META, RECOVER_META = "nasiko_search_tools", "nasiko_call_tool", "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

Purpose = Literal["read", "send"]
Source = Literal["tracker", "chat"]
SOURCES: tuple[Source, ...] = ("tracker", "chat")

# (purpose, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Purpose, Source, str], ...] = (
    ("read", "tracker", "search issues with no project"),
    ("read", "tracker", "search issues in progress not updated since a date"),
    ("read", "tracker", "search open issues created before a date"),
    ("read", "tracker", "search issues without an estimate"),
    ("read", "tracker", "list teams"),
    ("read", "tracker", "get an issue with its assignee"),
    ("read", "chat", "find a chat user by email"),
    ("read", "chat", "find a chat user by name"),
    ("send", "chat", "send a direct message to a chat user"),
)

_WORDS: Mapping[Source, tuple[str, ...]] = MappingProxyType({
    "tracker": ("issue", "project", "cycle", "estimate", "milestone", "roadmap", "backlog", "sprint"),
    "chat": ("chat", "channel", "conversation", "dm", "im", "direct message", "workspace member"),
})
# File-store and memory tools come back from every search; they read neither issues nor chat users.
_NOT_SOURCE_WORDS = frozenset({"file", "files", "folder", "folders", "upload", "download", "memory", "drive"})
_SEND_WORDS = frozenset({"send", "post"})


@dataclass(frozen=True)
class Catalog:
    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Source] = field(default_factory=dict)
    searched: bool = False

    @property
    def tools(self) -> tuple[McpTool, ...]:
        return _unique((*self.listed, *self.found))

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)

    def read_tools(self, source: Source) -> tuple[McpTool, ...]:
        """This source's read tools (what search found, else the listed ones), plus unplaced ones only when at
        least one tool is placed with the source; never a write and never a file-store or memory tool."""
        pool = [t for t in _unique(self.found or self.listed) if not looks_like_write(t)
                and not _NOT_SOURCE_WORDS & set(send_guard.name_tokens(t.name))]
        placed = [t for t in pool if self.source_of(t) == source]
        return tuple(t for t in pool if t in placed or (placed and self.source_of(t) is None))

    def send_tools(self) -> tuple[McpTool, ...]:
        """Chat (or unplaced) tools that send or post, best first: a name that also says message."""
        tools = [t for t in self.tools if send_guard.is_write_tool(t.name) and self.source_of(t) in ("chat", None)
                 and _SEND_WORDS & set(send_guard.name_tokens(t.name))]
        return tuple(sorted(tools, key=lambda t: 0 if "message" in send_guard.name_tokens(t.name) else 1))


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Sends ...")."""
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    for text in (" ".join(send_guard.name_tokens(tool.name)), tool.description.casefold()):
        matched = [s for s in SOURCES if any(re.search(rf"\b{re.escape(w)}(?:s|es)?\b", text) for w in _WORDS[s])]
        if len(matched) == 1:
            return matched[0]
    return None


async def discover(deps: TurnDeps, purpose: Purpose, extra_queries: Sequence[str] = ()) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the step without an LLM call."""
    if not deps.traceparent:
        return None, replies.NO_TRACE_CONTEXT
    empty = replies.NO_TRACKER if purpose == "read" else replies.NO_SEND_TOOL
    if deps.gateway is None:
        return None, empty
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, replies.GATEWAY_UNREACHABLE
    catalog = Catalog(listed=tuple(t for t in listed if t.name not in HIDDEN_META))
    if any(t.name == SEARCH_META for t in listed):
        catalog = await _search(deps, catalog, purpose, extra_queries)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    if not catalog.tools:
        return None, empty
    log.info("%s: listed %s; found %s", purpose, [t.name for t in catalog.listed], [t.name for t in catalog.found])
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, purpose: Purpose, extra: Sequence[str]) -> Catalog:
    queries = [(s, q) for p, s, q in DISCOVERY_QUERIES if p == purpose] + [(None, q) for q in extra]
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in queries), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    provenance: dict[str, Source] = {}
    answered = 0
    for (source, query), outcome in zip(queries, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found = (*found, *outcome)
        if source is not None:
            provenance = {**{t.name: source for t in outcome}, **provenance}
    if not answered:
        log.warning("every tool search failed; falling back to tools/list alone")
        return catalog
    return Catalog(listed=catalog.listed, found=_unique(found), provenance=MappingProxyType(provenance), searched=True)


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
    if raw["name"] in HIDDEN_META:
        return None
    schema = raw.get("inputSchema")
    return McpTool(name=raw["name"], description=str(raw.get("description") or ""),
                   input_schema=MappingProxyType(dict(schema) if isinstance(schema, Mapping) else {}))


def _unique(tools: Iterable[McpTool]) -> tuple[McpTool, ...]:
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
