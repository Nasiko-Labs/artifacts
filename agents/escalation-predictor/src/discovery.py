"""Per-request tool discovery for one step: `tools/list`, plus the gateway's search meta-tool asked that
step's fixed capability queries (concurrently, this request's traceparent, no LLM, no cache), merged by
name. Without the meta-tool (search off) or when every search fails, `tools/list` alone is used.

The model never sees the meta-tools: found tools are called directly by their real names. This agent is
read-only: a tool whose name or description reads as a write is never offered. No provider or tool is
named here."""

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
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("escalation.discovery")

SEARCH_META, CALL_META, RECOVER_META = "nasiko_search_tools", "nasiko_call_tool", "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

Step = Literal["tickets", "bugs"]
Source = Literal["desk", "tracker"]
SOURCES: tuple[Source, ...] = ("desk", "tracker")

# (step, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Step, Source, str], ...] = (
    ("tickets", "desk", "search open support tickets updated after a date"),
    ("tickets", "desk", "get a support ticket with all its comments"),
    ("tickets", "desk", "get the organization of a ticket requester with tags"),
    ("tickets", "desk", "get ticket metrics such as reopens and replies"),
    ("bugs", "tracker", "search open issues by text"),
    ("bugs", "tracker", "get an issue with its state and url"),
    ("bugs", "tracker", "list issues labelled bug"),
)

# Words that place a tool with one source; the name is checked before the description.
_WORDS: Mapping[Source, tuple[str, ...]] = MappingProxyType({
    "desk": ("ticket", "requester", "helpdesk", "help desk", "support desk", "macro"),
    "tracker": ("issue", "jql", "sprint", "epic", "changelog", "backlog", "worklog", "bug"),
})


@dataclass(frozen=True)
class Catalog:
    """This request's tools for one step: `listed` is `tools/list` minus meta-tools; `found` is what the
    step's queries returned; `searched` is False when search was absent or every query failed."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Source] = field(default_factory=dict)
    searched: bool = False

    @property
    def tools(self) -> tuple[McpTool, ...]:
        return _unique((*self.listed, *self.found))

    def read_tools(self) -> tuple[McpTool, ...]:
        """The step's read tools: what its queries found (else the listed tools), minus anything that reads as a write."""
        return tuple(t for t in _unique(self.found or self.listed) if not looks_like_write(t))

    def source_tools(self, source: Source) -> tuple[McpTool, ...]:
        """The step's read tools that their own name or description places with `source`. Strict on purpose:
        a search query can surface unrelated tools (file stores, memory), and those are never offered."""
        return tuple(t for t in self.read_tools() if classify(t) == source)

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool)


WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit",
    "delete", "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin",
    "react", "invite", "kick", "join", "leave", "rename", "upload", "insert", "write",
    "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "merge", "solve", "tag", "untag", "reopen",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    return any(token in WRITE_VERBS for token in name_tokens(tool_name))


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Creates ...")."""
    if is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


# Words that place a tool only when they are in its NAME (a description may say "conversation" of anything).
_NAME_ONLY: Mapping[Source, tuple[str, ...]] = MappingProxyType({"desk": ("conversation",), "tracker": ()})


def classify(tool: McpTool) -> Source | None:
    name = " ".join(name_tokens(tool.name))
    for text, extra in ((name, _NAME_ONLY), (tool.description.casefold(), None)):
        words = {s: (*_WORDS[s], *(extra[s] if extra else ())) for s in SOURCES}
        matched = [s for s in SOURCES if any(re.search(rf"\b{re.escape(w)}", text) for w in words[s])]
        if len(matched) == 1:
            return matched[0]
    return None


async def discover(deps: TurnDeps, step: Step, extra_queries: Sequence[str] = ()) -> tuple[Catalog | None, str | None]:
    """This request's catalog for `step`, or the fixed reply that ends the step without an LLM call."""
    if not deps.traceparent:
        return None, replies.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, replies.NO_TOOLS
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, replies.GATEWAY_UNREACHABLE
    catalog = Catalog(listed=tuple(t for t in listed if t.name not in HIDDEN_META))
    if any(t.name == SEARCH_META for t in listed):
        catalog = await _search(deps, catalog, step, extra_queries)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    if not catalog.tools:
        return None, replies.NO_TOOLS
    log.info("%s step: listed %s; found %s", step, [t.name for t in catalog.listed], [t.name for t in catalog.found])
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, step: Step, extra: Sequence[str]) -> Catalog:
    queries = [(s, q) for st, s, q in DISCOVERY_QUERIES if st == step] + [(None, q) for q in extra]
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
