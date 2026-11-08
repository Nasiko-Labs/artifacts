"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name. Adapted from the promise-tracker pattern; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first
message, so the reads each step needs may be missing from it. The agent asks the search meta-tool
itself, once per query, concurrently (this request's traceparent, so nothing is cached). No LLM call.
With tool search off the meta-tool is not listed and `tools/list` alone is used.

The model never sees the meta-tools, never sees a write tool, and each step is offered only its own
source's reads. No provider or tool is named here.
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

import prompts
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from readonly import CALL_META, looks_like_write

log = logging.getLogger("weekly_rollup.discovery")

SEARCH_META = "nasiko_search_tools"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})
SEARCH_LIMIT = 8

Source = Literal["tracker", "chat", "calendar"]
SOURCES: tuple[Source, ...] = ("tracker", "chat", "calendar")

# (source, query). Provider-neutral capability phrases; every purpose is a read.
DISCOVERY_QUERIES: tuple[tuple[Source, str], ...] = (
    ("tracker", "list teams or projects"),
    ("tracker", "get the current user's teams"),
    ("tracker", "search issues by team and updated date"),
    ("tracker", "list issues completed in a date range"),
    ("tracker", "get an issue with its comments and relations"),
    ("chat", "search messages by keyword in a date range"),
    ("chat", "list channels the user is a member of"),
    ("chat", "get messages from a channel"),
    ("calendar", "list calendar events in a date range"),
    ("calendar", "search events by title keyword"),
)

# Phrases that place a tool on one side: the name is checked first, then (with narrower phrases, since
# descriptions of unrelated tools mention messages, events or conversations) the description.
_WORDS: Mapping[Source, tuple[str, ...]] = MappingProxyType({
    "tracker": ("issue", "ticket", "sprint", "epic", "backlog", "cycle", "milestone"),
    "chat": ("chat", "channel", "conversation", "direct message", "message"),
    "calendar": ("calendar", "event", "meeting", "free busy", "freebusy"),
})
_DESCRIPTION_WORDS: Mapping[Source, tuple[str, ...]] = MappingProxyType({
    "tracker": ("issue", "ticket", "sprint", "epic"),
    "chat": ("chat channel", "channel", "direct message", "chat message"),
    "calendar": ("calendar",),
})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """This request's read tools. `found` is what the search queries returned, with the source of the
    query that found each tool in `provenance`; `searched` is False without search results."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Source] = field(default_factory=dict)
    searched: bool = False
    recover: McpTool | None = None

    @property
    def writes(self) -> frozenset[str]:
        return frozenset(t.name for t in (*self.listed, *self.found) if looks_like_write(t))

    def tools_for(self, source: Source) -> tuple[McpTool, ...]:
        """The reads one step may use; never a write. A source is available only when at least one read
        is recognised as that source by its own name or description (a search hit alone is not enough:
        the search also returns unrelated tools). Unrecognised tools the source's own queries found (or,
        without search results, every unrecognised listed tool) ride along as helpers."""
        pool = _unique((*self.found, *self.listed))
        own = [t for t in pool if classify(t) == source and not looks_like_write(t)]
        if not own:
            return ()
        if self.searched and self.found:
            helpers = [t for t in self.found if classify(t) is None and self.provenance.get(t.name) == source]
        else:
            helpers = [t for t in pool if classify(t) is None]
        reads = _unique((*own, *(t for t in helpers if not looks_like_write(t))))
        return (*reads, self.recover) if self.recover else reads

    def source_of(self, tool: McpTool) -> Source | None:
        return self.provenance.get(tool.name) or classify(tool)


def classify(tool: McpTool) -> Source | None:
    for text, words in ((_words(tool.name), _WORDS), (_words(tool.description), _DESCRIPTION_WORDS)):
        hits = [s for s in SOURCES if any(_has(text, w) for w in words[s])]
        if len(hits) == 1:
            return hits[0]
    return None


def _has(text: str, phrase: str) -> bool:
    """The phrase as whole words, singular or plural."""
    return any(f" {phrase}{suffix} " in text for suffix in ("", "s", "es"))


def _words(text: str) -> str:
    return f" {_NON_ALNUM.sub(' ', _CAMEL.sub(' ', text).casefold()).strip()} "


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_TOOLS
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", type(e).__name__)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if t.name not in HIDDEN_META and t.name != RECOVER_META)
    catalog = Catalog(listed=real, recover=recover)
    if SEARCH_META in names:
        catalog = await _search(deps, catalog)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    if not any(catalog.tools_for(s) for s in SOURCES):
        return None, prompts.NO_TOOLS
    log.info("tools per source: %s", {s: [t.name for t in catalog.tools_for(s)] for s in SOURCES})
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog) -> Catalog:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    provenance: dict[str, Source] = {}
    answered = 0
    for (source, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s", query, type(outcome).__name__)
            continue
        answered += 1
        found = (*found, *outcome)
        provenance = {**{t.name: source for t in outcome}, **provenance}  # first query wins
    if not answered:
        log.warning("every tool search failed; falling back to tools/list alone")
        return catalog
    return Catalog(listed=catalog.listed, found=_unique(found), provenance=MappingProxyType(provenance),
                   searched=True, recover=catalog.recover)


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
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
