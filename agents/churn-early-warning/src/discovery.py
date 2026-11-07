"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title (the
user's first message), so the read and send tools this agent needs may be missing from it. The agent
asks the search meta-tool itself, once per query, concurrently, on every request (the gateway needs
this request's traceparent, so nothing is cached). No LLM call is involved. With tool search off the
meta-tool is not listed and `tools/list` alone is used.

The model never sees the meta-tools: found tools are called directly by their real names, so the send
guard always sees the real call. No provider or tool is named here.

(Copied in once from the hardened kit; owned by this agent. The queries are this agent's: one read
discovery per turn covers subscriptions, changes, mail and the support desk.)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import prompts
import send_guard
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("churn.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})  # never offered to the model
SEARCH_LIMIT = 8

Purpose = Literal["read", "send"]
Source = Literal["payments", "email", "support"]
SOURCES: tuple[Source, ...] = ("payments", "email", "support")

# (purpose, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Purpose, Source, str], ...] = (
    # subscriptions and customers
    ("read", "payments", "list subscriptions with all statuses"),
    ("read", "payments", "list subscriptions renewing soon"),
    ("read", "payments", "list subscription items with price and quantity"),
    ("read", "payments", "retrieve a customer with email and name"),
    # changes
    ("read", "payments", "list events of subscription updates and cancellations"),
    ("read", "payments", "list events of failed payments"),
    # mail
    ("read", "email", "search received email from a domain or address"),
    ("read", "email", "search my sent email to an address"),
    ("read", "email", "fetch an email thread with all its messages"),
    # support desk
    ("read", "support", "search tickets by requester email"),
    ("read", "support", "list open tickets with priority"),
    # draft and send
    ("send", "email", "send an email or reply to a thread"),
)

# Words that place a tool on one side; the name is checked before the description.
_WORDS: dict[Source, tuple[str, ...]] = {
    "payments": ("subscription", "invoice", "payment", "billing", "charge", "price", "customer"),
    "email": ("mail", "inbox"),
    "support": ("ticket", "helpdesk", "support desk"),
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
        return _unique((*self.listed, *self.found.get("read", ()), *self.found.get("send", ())))

    def read_tools(self, source: Source | None = None) -> tuple[McpTool, ...]:
        """What a read step may use: the tools the read queries returned (or, without search results, the
        listed ones), minus anything that reads as a write. With `source`, that source's tools plus any
        tool no rule places (the model may still need it)."""
        pool = self.found.get("read") or self.listed
        reads = tuple(t for t in _unique(pool) if not looks_like_write(t))
        if source is not None:
            reads = tuple(t for t in reads if self.source_of(t) in (source, None))
        return (*reads, self.recover) if reads and self.recover else reads

    def send_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self.tools if send_guard.is_write_tool(t.name))

    def placed(self, tool: McpTool) -> Source | None:
        """The source the tool's own name or description names (search provenance is not evidence: a
        semantic search returns its nearest tools even when none fits)."""
        return classify(tool)

    def source_of(self, tool: McpTool) -> Source | None:
        """The tool's name first, then which query found it, then its description."""
        return _place(tool.name) or self.provenance.get(tool.name) or _place(tool.description)


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Sends ...")."""
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    return _place(tool.name) or _place(tool.description)


def _place(text: str) -> Source | None:
    lowered = text.casefold().replace("_", " ")
    hits = [source for source, words in _WORDS.items() if any(w in lowered for w in words)]
    return hits[0] if len(hits) == 1 else None


async def discover(deps: TurnDeps, purpose: Purpose,
                   extra_queries: Sequence[str] = ()) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_PAYMENTS_TOOL
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
        return None, prompts.NO_PAYMENTS_TOOL
    log.info("tools listed: %s; found by search: %s", [t.name for t in catalog.listed],
             {k: [t.name for t in v] for k, v in catalog.found.items()})
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
    return Catalog(listed=catalog.listed, found={purpose: _unique(found)}, provenance=MappingProxyType(provenance),
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
