"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name. Adapted from the promise-tracker pattern; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first
message, so the reads each step needs may be missing from it. The agent asks the search meta-tool
itself, once per query, concurrently (this request's traceparent, so nothing is cached). No LLM call.
With tool search off the meta-tool is not listed and `tools/list` alone is used.

The model never sees the meta-tools and never sees a write tool. Each step is offered the reads its
own queries found (all tracker reads when search found none). No provider or tool is named here.
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

from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from readonly import CALL_META, looks_like_write

log = logging.getLogger("scope_creep.discovery")

SEARCH_META = "nasiko_search_tools"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})
SEARCH_LIMIT = 8

Step = Literal["teams", "sprint", "issues", "history"]
STEPS: tuple[Step, ...] = ("teams", "sprint", "issues", "history")

# (step, query). Provider-neutral capability phrases; every purpose is a read.
DISCOVERY_QUERIES: tuple[tuple[Step, str], ...] = (
    ("teams", "list teams or projects"),
    ("teams", "get the current user's teams"),
    ("sprint", "list sprints or cycles for a team"),
    ("sprint", "get the active sprint or cycle with start and end dates"),
    ("issues", "list issues in a sprint or cycle"),
    ("issues", "search issues by sprint and team with estimate and assignee"),
    ("history", "get an issue's change history"),
    ("history", "list issue activity including when it was added to a sprint"),
)

_TRACKER_WORDS = ("issue", "ticket", "sprint", "cycle", "iteration", "epic", "backlog", "changelog", "team",
                  "project", "board", "milestone", "story", "estimate")
_HISTORY_WORDS = ("history", "histories", "activity", "activities", "changelog", "audit", "changes", "event")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """This request's tracker reads. `found_by` maps each searched tool to the steps whose queries found it."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    found_by: Mapping[str, frozenset[str]] = field(default_factory=dict)
    recover: McpTool | None = None

    @property
    def writes(self) -> frozenset[str]:
        return frozenset(t.name for t in (*self.listed, *self.found) if looks_like_write(t))

    @property
    def reads(self) -> tuple[McpTool, ...]:
        pool = _unique((*self.found, *self.listed))
        # Searched or listed, a read must be recognisably a tracker tool; without search results the
        # listed tools with no recognisable source are offered too (none mode).
        own = [t for t in pool if is_tracker(t) or (not self.found and not _other_source(t))]
        return tuple(t for t in own if not looks_like_write(t))

    @property
    def has_history(self) -> bool:
        return any(is_history(t) for t in self.reads)

    def tools_for(self, step: Step) -> tuple[McpTool, ...]:
        mine = tuple(t for t in self.reads if step in self.found_by.get(t.name, ()))
        if step == "history":
            mine = _unique((*mine, *(t for t in self.reads if is_history(t))))
        chosen = mine or self.reads
        return (*chosen, self.recover) if chosen and self.recover else chosen


def _words(text: str) -> str:
    return f" {_NON_ALNUM.sub(' ', _CAMEL.sub(' ', text).casefold()).strip()} "


def _has_any(text: str, phrases: Iterable[str]) -> bool:
    return any(f" {p}{suffix} " in text for p in phrases for suffix in ("", "s", "es"))


def is_tracker(tool: McpTool) -> bool:
    return _has_any(_words(tool.name), _TRACKER_WORDS) or _has_any(_words(tool.description), _TRACKER_WORDS)


def is_history(tool: McpTool) -> bool:
    return _has_any(_words(tool.name), _HISTORY_WORDS) or _has_any(_words(tool.description), _HISTORY_WORDS)


_OTHER_WORDS = ("mail", "email", "message", "channel", "chat", "calendar", "event", "file", "folder",
                "sheet", "spreadsheet", "pull request", "commit", "repo", "repository", "payment", "invoice")


def _other_source(tool: McpTool) -> bool:
    return _has_any(_words(tool.name), _OTHER_WORDS)


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or a failure kind: no_gateway | unreachable | no_tools."""
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", type(e).__name__)
        return None, "unreachable"
    names = {t.name for t in listed}
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if t.name not in HIDDEN_META and t.name != RECOVER_META)
    catalog = Catalog(listed=real, recover=recover)
    if SEARCH_META in names:
        catalog = await _search(deps, catalog)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    if not catalog.reads:
        return None, "no_tools"
    log.info("tracker reads: %s (history: %s)", [t.name for t in catalog.reads], catalog.has_history)
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog) -> Catalog:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    found_by: dict[str, frozenset[str]] = {}
    for (step, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s", query, type(outcome).__name__)
            continue
        found = (*found, *outcome)
        found_by = {**found_by, **{t.name: found_by.get(t.name, frozenset()) | {step} for t in outcome}}
    if not found_by:
        return catalog
    return Catalog(listed=catalog.listed, found=_unique(found), found_by=MappingProxyType(found_by),
                   recover=catalog.recover)


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
