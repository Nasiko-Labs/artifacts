"""Per-request tool discovery for one step: `tools/list`, plus the gateway's search meta-tool asked that
step's fixed capability queries (concurrently, this request's traceparent, no LLM, no cache), merged by
name. Without the meta-tool (search off) or when every search fails, `tools/list` alone is used.

The model never sees the meta-tools: found tools are called directly by their real names. This agent is
read-only: a tool that reads as a write is never offered to any step. No provider or tool is named here."""

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

log = logging.getLogger("feedback_to_roadmap.discovery")

SEARCH_META, CALL_META, RECOVER_META = "nasiko_search_tools", "nasiko_call_tool", "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

Step = Literal["tickets", "chat", "crm", "link"]
Source = Literal["desk", "chat", "crm", "tracker"]
SOURCES: tuple[Source, ...] = ("desk", "chat", "crm", "tracker")
STEP_SOURCE: Mapping[Step, Source] = MappingProxyType({"tickets": "desk", "chat": "chat", "crm": "crm",
                                                       "link": "tracker"})

# (step, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Step, Source, str], ...] = (
    ("tickets", "desk", "search support tickets or conversations created after a date"),
    ("tickets", "desk", "get a support ticket or conversation with its messages"),
    ("tickets", "desk", "get the organization or company of a support ticket requester"),
    ("chat", "chat", "search chat messages by text and date"),
    ("chat", "chat", "read recent messages of a chat channel"),
    ("chat", "chat", "list chat channels"),
    ("crm", "crm", "search companies by name or domain"),
    ("crm", "crm", "get a company with its properties"),
    ("crm", "crm", "list deals of a company with amount and stage"),
    ("link", "tracker", "search issues by text"),
    ("link", "tracker", "get an issue with its state and url"),
    ("link", "tracker", "list issues of a team"),
)

# Words that place a tool with one source; the name is checked before the description.
_WORDS: Mapping[Source, tuple[str, ...]] = MappingProxyType({
    "desk": ("ticket", "requester", "helpdesk", "help desk", "support desk", "macro"),
    "chat": ("channel", "chat", "message", "thread", "reaction", "permalink"),
    "crm": ("compan", "deal", "crm", "contact", "pipeline"),
    "tracker": ("issue", "cycle", "sprint", "epic", "backlog", "milestone"),
})
# Capabilities this agent never reads. A tool naming one of them (and none of ours) is never taken for a source,
# even when a capability search returned it: semantic search ranks the nearest tools, related or not.
_FOREIGN = ("file", "folder", "upload", "download", "drive", "memory", "calendar", "email", "mail", "spreadsheet",
            "document", "repositor", "commit", "pull request")
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove", "trash",
    "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "kick", "join", "leave", "rename",
    "upload", "insert", "write", "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel",
    "complete", "close", "assign", "approve", "transfer", "notify", "merge", "solve", "upsert",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def name_tokens(tool_name: str) -> tuple[str, ...]:
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    return any(token in WRITE_VERBS for token in name_tokens(tool_name))


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

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or (None if is_foreign(tool) else self.provenance.get(tool.name))

    def step_tools(self, source: Source) -> tuple[McpTool, ...]:
        """The read tools that belong to `source`: offered to that step and nothing else."""
        return tuple(t for t in self.read_tools() if self.source_of(t) == source)


def is_foreign(tool: McpTool) -> bool:
    return _mentions(f"{' '.join(name_tokens(tool.name))} {tool.description.casefold()}", _FOREIGN)


def _mentions(text: str, words: Iterable[str]) -> bool:
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Creates ...")."""
    if is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    """The name decides first; a name that names a capability this agent never reads (a file store, memory)
    is never placed by its description, which may mention "chat" or "message" in passing."""
    name = " ".join(name_tokens(tool.name))
    texts = (name,) if _mentions(name, _FOREIGN) else (name, tool.description.casefold())
    for text in texts:
        matched = [s for s in SOURCES if any(re.search(rf"\b{re.escape(w)}", text) for w in _WORDS[s])]
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
