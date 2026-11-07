"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked each step's
capability queries (concurrently, no LLM, no cache), merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first message;
the agent asks the search meta-tool itself. With search off the meta-tool is not listed and `tools/list`
alone is used. The model never sees the meta-tools: found tools are called by their real names, so the
send guard always sees the real call. No provider or tool is named here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import prompts
import send_guard
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("call_miner.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})
SEARCH_LIMIT = 8

Step = Literal["calls", "accounts", "notes", "digest"]
Source = Literal["calls", "crm", "chat"]
STEP_SOURCE: Mapping[str, Source] = MappingProxyType(
    {"calls": "calls", "accounts": "crm", "notes": "crm", "digest": "chat"})

# Provider-neutral capability phrases per step; tune here.
QUERIES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "calls": ("list recorded calls in a date range", "get call participants and details",
              "get the transcript of a call"),
    "accounts": ("search companies by domain", "get a company with its deals and properties",
                 "list deals of a company"),
    "notes": ("create a note on a company record", "associate a note with a company record"),
    "digest": ("find a channel by name or id", "post a message to a channel"),
})

# Words that place a tool on one source; checked in this order, name before description.
_WORDS: tuple[tuple[Source, tuple[str, ...]], ...] = (
    ("crm", ("crm", "compan", "deal", "contact", "engagement", "pipeline", "note")),
    ("calls", ("transcript", "recording", "recorded", "call")),
    ("chat", ("channel", "chat", "conversation", "message")),
)


@dataclass(frozen=True)
class Catalog:
    """This request's tools for one step. `found` is what the step's queries returned, in rank order."""

    step: str
    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Source] = field(default_factory=dict)
    recover: McpTool | None = None

    @property
    def source(self) -> Source:
        return STEP_SOURCE[self.step]

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)

    def _pool(self) -> tuple[McpTool, ...]:
        own = tuple(t for t in self.found if self.source_of(t) == self.source)
        if own:
            return own
        return tuple(t for t in self.listed if self.source_of(t) in (self.source, None))

    def read_tools(self) -> tuple[McpTool, ...]:
        """The step's tools minus anything that reads as a write; the recovery helper when listed."""
        reads = tuple(t for t in self._pool() if not looks_like_write(t))
        return (*reads, self.recover) if reads and self.recover else reads

    def write_tool(self, *wanted: frozenset[str], banned: frozenset[str] = frozenset()) -> McpTool | None:
        """The first write tool of this step's source (search rank order) whose name tokens include one
        token from each `wanted` set and none from `banned`."""
        pool = _unique((*self._pool(), *(t for t in self.listed if self.source_of(t) == self.source)))
        for tool in pool:
            tokens = set(send_guard.name_tokens(tool.name))
            if send_guard.is_write_tool(tool.name) and all(tokens & group for group in wanted) and not tokens & banned:
                return tool
        return None

    def named(self, name: str) -> McpTool | None:
        return next((t for t in (*self.found, *self.listed) if t.name == name), None)


def looks_like_write(tool: McpTool) -> bool:
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    for text in (tool.name.casefold(), tool.description.casefold()):
        for source, words in _WORDS:
            if any(w in text for w in words):
                return source
    return None


async def discover(deps: TurnDeps, step: Step, extra: Iterable[str] = ()) -> tuple[Catalog | None, str | None]:
    """This request's catalog for one step, or the fixed reply that ends the step without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_CALLS_TOOL
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if t.name not in HIDDEN_META and t.name != RECOVER_META)
    catalog = Catalog(step=step, listed=real, recover=recover)
    if any(t.name == SEARCH_META for t in listed):
        catalog = await _search(deps, catalog, (*QUERIES[step], *extra))
    log.info("step %s: listed %s; found %s", step, [t.name for t in catalog.listed], [t.name for t in catalog.found])
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, queries: tuple[str, ...]) -> Catalog:
    outcomes = await asyncio.gather(*(_ask(deps, q) for q in queries), return_exceptions=True)
    answered = [o for o in outcomes if not isinstance(o, BaseException)]
    for query, outcome in zip(queries, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
    if not answered:
        log.warning("every tool search failed; using tools/list alone")
        return catalog
    found = _unique(t for tools in answered for t in tools)
    provenance = MappingProxyType({t.name: catalog.source for t in found})
    return Catalog(step=catalog.step, listed=catalog.listed, found=found, provenance=provenance,
                   recover=catalog.recover)


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
