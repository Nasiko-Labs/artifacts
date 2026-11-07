"""Per-step tool discovery: `tools/list`, plus the gateway's search meta-tool asked this step's fixed
capability queries (concurrently, `limit` 8), merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title, so a step's
tools may be missing from it; the agent asks the search meta-tool itself, with this request's traceparent,
and calls every found tool directly by its real name. The model never sees a meta-tool. With tool search
off the meta-tool is not listed and `tools/list` alone is used, with the same code.

Which source a tool serves is decided by provenance first (the step query that found it), then by
capability words in its name, then its description. No provider or tool is named here.
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

log = logging.getLogger("incident_copilot.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})
SEARCH_LIMIT = 8

Step = Literal["incident", "metrics", "logs", "errors", "changes", "history", "runbook", "post"]
STEP_SOURCE: Mapping[str, str] = MappingProxyType({
    "incident": "alerting", "metrics": "metrics", "logs": "logs", "errors": "errors", "changes": "code",
    "history": "alerting", "runbook": "document", "post": "chat",
})

DISCOVERY_QUERIES: tuple[tuple[str, str], ...] = (
    ("incident", "get an incident by id with its details"),
    ("incident", "list open or triggered incidents for a service"),
    ("incident", "list alerts of an incident"),
    ("incident", "get a service's details"),
    ("metrics", "query a metric time series over a time range"),
    ("metrics", "list monitors for a service"),
    ("logs", "search log events for a service in a time range"),
    ("errors", "list error issues for a project in a time range"),
    ("errors", "get an error issue with its stack trace"),
    ("errors", "get event counts over time for an error issue"),
    ("changes", "list deployments of a repository"),
    ("changes", "list merged pull requests in a repository"),
    ("changes", "list commits in a repository since a time"),
    ("changes", "get a commit or pull request with its changed files"),
    ("changes", "list repositories"),
    ("history", "search past resolved incidents"),
    ("history", "list notes of an incident"),
    ("runbook", "search documents by title"),
    ("runbook", "get a document's content"),
    ("post", "post a message to a chat channel"),
    ("post", "find a chat channel by name or id"),
    ("post", "look up a chat user by email"),
)

# (name tokens, description phrases) per source.
SOURCE_WORDS: Mapping[str, tuple[frozenset[str], tuple[str, ...]]] = MappingProxyType({
    "alerting": (frozenset({"incident", "incidents", "alert", "alerts", "oncall", "oncalls", "escalation"}),
                 ("incident", "alert", "on-call")),
    "metrics": (frozenset({"metric", "metrics", "timeseries", "monitor", "monitors"}),
                ("metric", "time series", "timeseries", "monitor")),
    "logs": (frozenset({"log", "logs"}), (" log ", " logs", "log event", "log line")),
    "errors": (frozenset({"error", "errors", "exception", "exceptions", "stacktrace", "crash", "crashes"}),
               ("error issue", "error tracking", "exception", "stack trace", "crash")),
    "code": (frozenset({"repository", "repositories", "repo", "repos", "pull", "commit", "commits",
                        "deployment", "deployments", "branch"}),
             ("repository", "pull request", "commit", "deployment")),
    "document": (frozenset({"document", "documents", "doc", "docs", "wiki", "page", "pages"}),
                 ("document", "wiki", "knowledge base")),
    "chat": (frozenset({"chat", "channel", "channels", "conversation", "conversations"}),
             ("chat", "channel", "conversation")),
})


@dataclass(frozen=True)
class Catalog:
    """One step's tools. `listed` is `tools/list` (meta-tools removed); `found` is what this step's queries returned."""

    step: str
    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, str] = field(default_factory=dict)
    searched: bool = False
    recover: McpTool | None = None

    @property
    def source(self) -> str:
        return STEP_SOURCE[self.step]

    def source_of(self, tool: McpTool) -> str | None:
        return self.provenance.get(tool.name) or classify(tool)

    def _pool(self) -> tuple[McpTool, ...]:
        pool = self.found or self.listed
        return tuple(t for t in unique(pool) if self.source_of(t) in (self.source, None))

    def read_tools(self) -> tuple[McpTool, ...]:
        """What a read step may use: this step's tools, never anything that reads as a write."""
        return tuple(t for t in self._pool() if not looks_like_write(t))

    def write_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self._pool() if looks_like_write(t))


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Posts ...")."""
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS)


def classify(tool: McpTool) -> str | None:
    tokens = set(send_guard.name_tokens(tool.name))
    by_name = [s for s, (words, _) in SOURCE_WORDS.items() if tokens & words]
    if len(by_name) == 1:
        return by_name[0]
    text = f" {tool.description.casefold()} "
    by_text = [s for s, (_, phrases) in SOURCE_WORDS.items() if any(p in text for p in phrases)]
    return by_text[0] if len(by_text) == 1 else None


async def discover(deps: TurnDeps, step: str, extra_queries: tuple[str, ...] = ()) -> tuple[Catalog | None, str | None]:
    """This step's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_GATEWAY
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if t.name not in HIDDEN_META and t.name != RECOVER_META)
    catalog = Catalog(step=step, listed=real, recover=recover)
    if SEARCH_META in names:
        catalog = await _search(deps, catalog, extra_queries)
    log.info("step %s: listed %s; found %s", step, [t.name for t in catalog.listed], [t.name for t in catalog.found])
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, extra: tuple[str, ...]) -> Catalog:
    queries = [q for s, q in DISCOVERY_QUERIES if s == catalog.step] + list(extra)
    outcomes = await asyncio.gather(*(_ask(deps, q) for q in queries), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    answered = 0
    for query, outcome in zip(queries, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found = (*found, *outcome)
    if not answered:
        log.warning("every tool search failed for step %s; using tools/list alone", catalog.step)
        return catalog
    provenance = MappingProxyType({t.name: catalog.source for t in found})
    return Catalog(step=catalog.step, listed=catalog.listed, found=unique(found), provenance=provenance,
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


def unique(tools: Iterable[McpTool]) -> tuple[McpTool, ...]:
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
