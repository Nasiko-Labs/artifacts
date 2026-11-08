"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title (the
user's first message), so the read and post tools this agent needs may be missing from it. The agent
asks the search meta-tool itself, once per query, concurrently, on every request (the gateway needs
this request's traceparent, so nothing is cached). No LLM call is involved. With tool search off the
meta-tool is not listed and `tools/list` alone is used.

The model never sees the meta-tools: found tools are called directly by their real names, so tool health
always sees the real call. No provider or tool is named here.

(Copied in once from the hardened kit; owned by this agent. The queries are this agent's: locate and read
the inventory sheet, search chat for complaints. This agent is read-only: write tools are never offered.)
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
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("trust_checker.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})  # never offered to the model
SEARCH_LIMIT = 8

Purpose = Literal["read"]
Source = Literal["spreadsheet", "chat"]

# (purpose, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Purpose, Source, str], ...] = (
    ("read", "spreadsheet", "search spreadsheets by title"),
    ("read", "spreadsheet", "get spreadsheet sheet names and metadata"),
    ("read", "spreadsheet", "read the values of a spreadsheet range"),
    ("read", "chat", "search chat messages containing a phrase"),
    ("read", "chat", "fetch recent messages in a chat channel"),
)

# A tool whose name carries one of these verbs (or whose description opens with one) changes something.
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "append",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write", "batch",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "replace", "clear", "overwrite", "sends", "posts",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

# Words that place a tool on one side; the name is checked before the description.
_SOURCE_WORDS: dict[str, tuple[str, ...]] = {
    "spreadsheet": ("spreadsheet", "sheet"),
    "chat": ("channel", "message", "chat", "conversation"),
}


@dataclass(frozen=True)
class Catalog:
    """This request's tools. `listed` is `tools/list` (meta-tools removed); `found` holds what each
    search purpose returned; `searched` is False when the search meta-tool was absent or every query failed."""

    listed: tuple[McpTool, ...]
    found: Mapping[Purpose, tuple[McpTool, ...]] = field(default_factory=dict)
    provenance: Mapping[str, Source] = field(default_factory=dict)
    searched: bool = False
    recover: McpTool | None = None

    @property
    def tools(self) -> tuple[McpTool, ...]:
        """Every real tool, deduplicated by name: listed first, then found."""
        return _unique((*self.listed, *self.found.get("read", ())))

    def read_tools(self) -> tuple[McpTool, ...]:
        """What the read steps may use: the tools the read queries returned (or, without search
        results, the listed ones), minus anything that reads as a write. Each step then keeps only the
        tools whose own name or description places them in its source (`classify`)."""
        pool = self.found.get("read") or self.listed
        reads = tuple(t for t in _unique(pool) if not looks_like_write(t))
        return (*reads, self.recover) if reads and self.recover else reads

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Sends ...")."""
    if is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


def name_tokens(tool_name: str) -> tuple[str, ...]:
    """The words of a tool name: split on camel case and on anything not a letter or digit."""
    spaced = _CAMEL_BOUNDARY.sub(" ", tool_name)
    return tuple(t.casefold() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    return any(token in WRITE_VERBS for token in name_tokens(tool_name))


def classify(tool: McpTool) -> Source | None:
    """The one source whose words the name (else the description) carries; None when none or several."""
    for text in (tool.name.casefold(), tool.description.casefold()):
        hits = [source for source, words in _SOURCE_WORDS.items() if any(w in text for w in words)]
        if len(hits) == 1:
            return hits[0]
    return None


async def discover(deps: TurnDeps, purposes: Sequence[Purpose] = ("read",),
                   extra_queries: Sequence[str] = ()) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_TOOLS
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
        catalog = await _search(deps, catalog, purposes, extra_queries)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    if not catalog.tools:
        return None, prompts.NO_TOOLS
    log.info("tools listed: %s; found by search: %s", [t.name for t in catalog.listed],
             {k: [t.name for t in v] for k, v in catalog.found.items()})
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, purposes: Sequence[Purpose], extra: Sequence[str]) -> Catalog:
    queries = [(p, s, q) for p, s, q in DISCOVERY_QUERIES if p in purposes]
    queries += [(purposes[-1], None, q) for q in extra]
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, _, q in queries), return_exceptions=True)
    found: dict[str, tuple[McpTool, ...]] = {}
    provenance: dict[str, Source] = {}
    answered = 0
    for (purpose, source, query), outcome in zip(queries, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found = {**found, purpose: (*found.get(purpose, ()), *outcome)}
        if source is not None:
            provenance = {**{t.name: source for t in outcome}, **provenance}
    if not answered:
        log.warning("every tool search failed; falling back to tools/list alone")
        return catalog
    return Catalog(listed=catalog.listed, found=MappingProxyType({k: _unique(v) for k, v in found.items()}),
                   provenance=MappingProxyType(provenance), searched=True, recover=catalog.recover)


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
