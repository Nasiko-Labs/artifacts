"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked each step's
capability queries, merged by name (copied from promise-tracker and adapted to steps and sources).

With tool search on, `tools/list` holds only a few tools ranked against the flow title, so the agent
asks the search meta-tool itself, once per query, concurrently, with this request's traceparent (no
LLM call, no cache). With search off the meta-tool is not listed and `tools/list` alone is used.

Each step is offered only tools of its own source (or of no recognisable source); read steps never
see a write tool. The model never sees the meta-tools. No provider or tool is named here.
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

log = logging.getLogger("postmortem.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

Source = Literal["tracker", "document", "alerting", "chat"]


@dataclass(frozen=True)
class Step:
    source: Source
    write: bool
    queries: tuple[str, ...]


STEPS: Mapping[str, Step] = MappingProxyType({
    "find": Step("tracker", False, ("search issues with a query", "get an issue with its links and subtasks",
                                    "list issue types or labels of a project")),
    "find_docs": Step("document", False, ("search documents by title", "get a document's content")),
    "status": Step("tracker", False, ("search issues with a query",
                                      "get an issue with its status, assignee and history",
                                      "list comments of an issue",
                                      "get development information or linked pull requests of an issue")),
    "repeats": Step("alerting", False, ("search incidents created after a date", "get an incident by id")),
    "lookup": Step("chat", False, ("look up a chat user by email",)),
    "nudge": Step("chat", True, ("open a direct message conversation with a user",
                                 "post a message to a chat conversation")),
    "transitions": Step("tracker", False, ("list available status transitions of an issue",)),
    "reopen": Step("tracker", True, ("transition an issue to a new status", "add a comment to an issue")),
})
TURN_STEPS = ("find", "find_docs", "status", "repeats")

# Words that place a tool on one side. Names are read first (all words), then descriptions (narrower).
_NAME_WORDS: Mapping[str, frozenset[str]] = MappingProxyType({
    "tracker": frozenset({"issue", "issues", "ticket", "tickets", "epic", "epics", "sprint", "backlog",
                          "transition", "transitions", "worklog"}),
    "document": frozenset({"document", "documents", "doc", "docs", "page", "pages", "wiki", "file", "files"}),
    "alerting": frozenset({"incident", "incidents", "alert", "alerts", "oncall", "escalation", "outage"}),
    "chat": frozenset({"chat", "channel", "channels", "conversation", "conversations", "dm", "im"}),
})
_DESCRIPTION_WORDS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "tracker": ("issue", "ticket", "epic"),
    "document": ("document", "wiki"),
    "alerting": ("incident", "alert", "on-call"),
    "chat": ("chat", "channel", "direct message"),
})

# Which write tool does what, by its name's words.
ROLES: Mapping[str, tuple[frozenset[str], frozenset[str]]] = MappingProxyType({
    "message": (frozenset({"post", "send"}), frozenset({"message", "messages", "msg", "chat"})),
    "open_dm": (frozenset({"open"}), frozenset({"dm", "im", "conversation", "conversations", "direct"})),
    "transition": (frozenset({"transition"}), frozenset({"transition", "issue", "issues", "ticket"})),
    "comment": (frozenset({"add", "create", "post"}), frozenset({"comment", "comments"})),
})


def classify(tool: McpTool) -> Source | None:
    tokens = set(send_guard.name_tokens(tool.name))
    by_name = [s for s, words in _NAME_WORDS.items() if tokens & words]
    if len(by_name) == 1:
        return by_name[0]
    text = tool.description.casefold()
    by_text = [s for s, words in _DESCRIPTION_WORDS.items() if any(w in text for w in words)]
    return by_text[0] if len(by_text) == 1 else None


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Sends ...")."""
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS)


def role_of(tool: McpTool) -> str | None:
    tokens = set(send_guard.name_tokens(tool.name))
    return next((r for r, (verbs, nouns) in ROLES.items() if tokens & verbs and tokens & nouns), None)


@dataclass(frozen=True)
class Catalog:
    """This request's tools: `listed` (meta-tools removed), what each step's queries `found`, and the
    source of the query that found each tool."""

    listed: tuple[McpTool, ...]
    found: Mapping[str, tuple[McpTool, ...]] = field(default_factory=dict)
    provenance: Mapping[str, Source] = field(default_factory=dict)
    searched: bool = False

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)

    def tools_for(self, step: str) -> tuple[McpTool, ...]:
        spec = STEPS[step]
        pool = (*self.found.get(step, ()), *self.listed) if spec.write else (self.found.get(step) or self.listed)
        fits = [t for t in _unique(pool) if self.source_of(t) in (spec.source, None)]
        if spec.write:
            return tuple(t for t in fits if send_guard.is_write_tool(t.name))
        return tuple(t for t in fits if not looks_like_write(t))

    def has_source(self, step: str) -> bool:
        """Some offered tool positively belongs to this step's source."""
        return any(self.source_of(t) == STEPS[step].source for t in self.tools_for(step))

    def pick(self, step: str, role: str) -> McpTool | None:
        return next((t for t in self.tools_for(step) if role_of(t) == role), None)


async def discover(deps: TurnDeps, steps: Sequence[str],
                   extra_queries: Sequence[str] = ()) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_TRACKER
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    catalog = Catalog(listed=tuple(t for t in listed if t.name not in HIDDEN_META))
    if any(t.name == SEARCH_META for t in listed):
        catalog = await _search(deps, catalog, steps, extra_queries)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    log.info("tools listed: %s; found: %s", [t.name for t in catalog.listed],
             {k: [t.name for t in v] for k, v in catalog.found.items()})
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, steps: Sequence[str], extra: Sequence[str]) -> Catalog:
    asks = [(s, q) for s in steps for q in STEPS[s].queries] + [(steps[-1], q) for q in extra]
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in asks), return_exceptions=True)
    found: dict[str, tuple[McpTool, ...]] = {}
    provenance: dict[str, Source] = {}
    answered = 0
    for (step, query), outcome in zip(asks, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found[step] = _unique((*found.get(step, ()), *outcome))
        for tool in outcome:
            provenance.setdefault(tool.name, STEPS[step].source)
    if not answered:
        log.warning("every tool search failed; falling back to tools/list alone")
        return catalog
    return Catalog(listed=catalog.listed, found=MappingProxyType(found), provenance=MappingProxyType(provenance),
                   searched=True)


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
