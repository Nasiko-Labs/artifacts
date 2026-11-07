"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name. Adapted from the promise-tracker agent; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title (the
user's first message), so "prep me for today" may list calendar tools but no mail, chat or document
reads. The agent asks the search meta-tool itself, once per query, concurrently, per step (the
gateway needs this request's traceparent, so nothing is cached). No LLM call is involved. With tool
search off the meta-tool is not listed and `tools/list` alone is used.

This agent is read-only: anything that reads as a write is never offered to the model. The meta-tools
are never offered either. No provider or tool is named here.
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

import prep_prompts as prompts
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("meeting_prep.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})  # never offered to the model
SEARCH_LIMIT = 8

Purpose = Literal["calendar", "context"]
Source = Literal["calendar", "email", "chat", "document", "ticket"]
SOURCES: tuple[Source, ...] = ("calendar", "email", "chat", "document", "ticket")

# (purpose, source, query). Provider-neutral capability phrases, all reads; tune here.
DISCOVERY_QUERIES: tuple[tuple[Purpose, Source, str], ...] = (
    ("calendar", "calendar", "list my calendar events between two times"),
    ("calendar", "calendar", "get a calendar event with attendees and description"),
    ("calendar", "calendar", "get my calendar settings or time zone"),
    ("context", "email", "search email messages with a person"),
    ("context", "email", "fetch an email thread with all its messages"),
    ("context", "chat", "search chat messages with a person"),
    ("context", "chat", "read messages in a direct message conversation"),
    ("context", "chat", "fetch replies in a chat thread"),
    ("context", "document", "read a document's content by link or id"),
    ("context", "ticket", "get a tracker ticket or issue by key"),
    ("context", "ticket", "search tracker issues"),
    ("context", "calendar", "list my calendar events between two times"),
)

# Words that place a tool in one source; the name is checked before the description, and a text that
# matches more than one source decides nothing.
_SOURCE_WORDS: Mapping[Source, tuple[str, ...]] = MappingProxyType({
    "calendar": ("calendar", "event"),
    "email": ("mail", "inbox"),
    "chat": ("chat", "channel", "conversation", "direct message"),
    "document": ("document", "docs", "wiki", "page", "spreadsheet"),
    "ticket": ("ticket", "issue", "tracker"),
})

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove",
    "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "kick", "join",
    "leave", "rename", "upload", "insert", "write", "publish", "schedule", "star", "unstar", "share",
    "submit", "import", "cancel", "complete", "close", "assign", "approve", "transfer", "notify", "accept",
    "decline", "respond", "rsvp", "quick", "clear", "duplicate", "copy", "transition", "comment", "draft",
})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """This request's tools. `listed` is `tools/list` (meta-tools removed); `found` holds what this
    purpose's search queries returned; `searched` is False when search was absent or every query failed."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Source] = field(default_factory=dict)
    searched: bool = False
    recover: McpTool | None = None

    def read_tools(self, sources: Iterable[Source] | None = None) -> tuple[McpTool, ...]:
        """Read-only tools for a step: what search returned (or, without results, what was listed),
        merged with the listed tools of the wanted sources; never a write. `recover_compressed` is
        added when anything is offered."""
        wanted = set(sources) if sources is not None else None
        pool = _unique((*self.found, *self.listed)) if self.found else self.listed
        reads = tuple(t for t in pool if not looks_like_write(t)
                      and (wanted is None or self.source_of(t) in wanted))
        return (*reads, self.recover) if reads and self.recover else reads

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)


def name_tokens(tool_name: str) -> tuple[str, ...]:
    """`c_1a2b__MAIL_createDraft` → ("c", "1a2b", "mail", "create", "draft")."""
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def looks_like_write(tool: McpTool) -> bool:
    """The name holds a write verb, or the description opens with one ("Sends ...", "Creates ...")."""
    tokens = name_tokens(tool.name)
    if any(token in WRITE_VERBS for token in tokens) or {"chat", "message"} <= set(tokens):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    for text in (tool.name.casefold().replace("_", " "), tool.description.casefold()):
        matched = [s for s, words in _SOURCE_WORDS.items() if any(w in text for w in words)]
        if len(matched) == 1:
            return matched[0]
    return None


async def discover(deps: TurnDeps, purpose: Purpose) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_CALENDAR_TOOL
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
        catalog = await _search(deps, catalog, purpose)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    log.info("%s tools listed: %s; found by search: %s", purpose, [t.name for t in catalog.listed],
             [t.name for t in catalog.found])
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, purpose: Purpose) -> Catalog:
    queries = [(s, q) for p, s, q in DISCOVERY_QUERIES if p == purpose]
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
