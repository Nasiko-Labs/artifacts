"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries for the step at hand, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title, so the
tools a step needs may be missing from it. The agent asks the search meta-tool itself, once per query,
concurrently, on every request (the gateway needs this request's traceparent, so nothing is cached).
No LLM call is involved. With tool search off the meta-tool is not listed and `tools/list` alone is used.

The model never sees the meta-tools: found tools are called directly by their real names, so the send
guard always sees the real call. No provider or tool is named here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import prompts
import send_guard
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("commitment_audit.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})  # never offered to the model
SEARCH_LIMIT = 8

Purpose = Literal["source_tracker", "source_plan", "tracker", "chat", "channel", "write_doc", "write_chat"]
Source = Literal["tracker", "document", "chat"]

# (purpose, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Purpose, Source, str], ...] = (
    ("source_tracker", "tracker", "search tracker issues with a query"),
    ("source_tracker", "tracker", "list child issues of an epic"),
    ("source_tracker", "tracker", "get a tracker issue with fields and links"),
    ("source_plan", "document", "read a document's content by link or id"),
    ("source_plan", "document", "read rows from a spreadsheet"),
    ("source_plan", "tracker", "get a tracker issue with fields and links"),
    ("tracker", "tracker", "get a tracker issue with fields and links"),
    ("tracker", "tracker", "search tracker issues with a query"),
    ("tracker", "tracker", "list child issues of an epic"),
    ("tracker", "tracker", "get an issue's change history or comments"),
    ("chat", "chat", "search chat messages"),
    ("chat", "chat", "fetch replies in a chat thread"),
    ("channel", "chat", "find a chat channel by name"),
    ("write_doc", "document", "create a new document with content"),
    ("write_chat", "chat", "post a chat message to a channel"),
)

# Words that place a tool on one side (whole words of the name, then of the description).
_SOURCE_WORDS: Mapping[Source, frozenset[str]] = MappingProxyType({
    "tracker": frozenset({"issue", "issues", "ticket", "tickets", "epic", "epics", "tracker", "sprint", "sprints",
                          "backlog", "changelog"}),
    "document": frozenset({"document", "documents", "doc", "docs", "spreadsheet", "spreadsheets", "sheet", "sheets",
                           "page", "pages", "wiki", "drive", "file", "files"}),
    "chat": frozenset({"chat", "channel", "channels", "conversation", "conversations", "message", "messages",
                       "thread", "threads"}),
})
_READ_VERBS = frozenset({"search", "get", "list", "fetch", "find", "read", "retrieve", "query", "lookup"})
_WORD = re.compile(r"[a-z]+")
# Choosing the one write tool for a target: what it must do, what it must not.
_WRITE_WANT: Mapping[str, tuple[frozenset[str], frozenset[str]]] = MappingProxyType({
    "document": (frozenset({"create"}), frozenset({"document", "doc", "page"})),
    "chat": (frozenset({"post", "send"}), frozenset({"message"})),
})
_WRITE_AVOID = frozenset({"schedule", "scheduled", "reminder", "ephemeral", "delete", "remove", "update", "edit",
                          "me", "draft", "comment", "copy", "template"})


@dataclass(frozen=True)
class Catalog:
    """This request's tools. `listed` is `tools/list` (meta-tools removed); `found` holds what the
    search queries returned; `searched` is False when the search meta-tool was absent or every query failed."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Source] = field(default_factory=dict)
    searched: bool = False
    recover: McpTool | None = None

    @property
    def tools(self) -> tuple[McpTool, ...]:
        return _unique((*self.listed, *self.found))

    def read_tools(self, sources: Iterable[Source]) -> tuple[McpTool, ...]:
        """What a read step may use: the tools its queries returned (or, without search results, the listed
        ones) that belong to `sources` or to no recognisable source, minus anything that reads as a write."""
        wanted = set(sources)
        pool = self.found or self.listed
        reads = tuple(t for t in _unique(pool) if not looks_like_write(t) and self.source_of(t) in (*wanted, None))
        return (*reads, self.recover) if reads and self.recover else reads

    def write_tool(self, source: Source) -> McpTool | None:
        """The one write tool for this target, chosen in code by what its name says it does."""
        candidates = [t for t in self.tools if send_guard.is_write_tool(t.name) and self.source_of(t) in (source, None)]
        scored = [(score, n, t) for n, t in enumerate(candidates) if (score := _write_score(t, source)) > 0]
        return min(scored, key=lambda x: (-x[0], x[1]))[2] if scored else None

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)


def _write_score(tool: McpTool, source: Source) -> int:
    tokens = set(send_guard.name_tokens(tool.name))
    verbs, nouns = _WRITE_WANT[source]
    if not tokens & verbs or tokens & _WRITE_AVOID:
        return 0
    return 2 + 2 * bool(tokens & nouns) + int("markdown" in tokens)


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Creates ..."). A name that
    also carries a read verb and whose description opens with one ("search ... post") is a read."""
    first = _first_word(tool.description)
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    if send_guard.is_write_tool(tool.name):
        reads = set(send_guard.name_tokens(tool.name)) & _READ_VERBS
        return not (reads and stems & _READ_VERBS)
    return bool(stems & send_guard.WRITE_VERBS)


def _first_word(description: str) -> str:
    words = description.strip().split(maxsplit=1)
    return words[0].strip(".,:;").casefold() if words else ""


def classify(tool: McpTool) -> Source | None:
    for words in (set(send_guard.name_tokens(tool.name)), set(_WORD.findall(tool.description.casefold()))):
        matched = [source for source, vocabulary in _SOURCE_WORDS.items() if words & vocabulary]
        if len(matched) == 1:
            return matched[0]
    return None


async def discover(deps: TurnDeps, purpose: Purpose,
                   extra_queries: Sequence[str] = ()) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_TRACKER_TOOL
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if t.name not in HIDDEN_META and t.name != RECOVER_META)
    catalog = Catalog(listed=real, recover=recover)
    if SEARCH_META in names:
        catalog = await _search(deps, catalog, purpose, extra_queries)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    if not catalog.tools:
        return None, prompts.NO_TRACKER_TOOL
    log.info("%s: tools listed %s; found by search %s", purpose, [t.name for t in catalog.listed],
             [t.name for t in catalog.found])
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
    """First occurrence of each name wins, in order."""
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
