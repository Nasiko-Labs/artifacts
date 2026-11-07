"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name. Adapted from the MCP kit pattern; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first
message, so the reads each step needs may be missing from it. The agent asks the search meta-tool
itself, once per query, concurrently (this request's traceparent, so nothing is cached). No LLM call.
With tool search off the meta-tool is not listed and `tools/list` alone is used.

The model never sees the meta-tools and never sees a write tool. No provider or tool is named here.
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

import prompts
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from readonly import CALL_META, looks_like_write

log = logging.getLogger("catchup.discovery")

SEARCH_META = "nasiko_search_tools"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

Step = Literal["identity", "channels", "history"]
STEPS: tuple[Step, ...] = ("identity", "channels", "history")

# (step, query). Provider-neutral capability phrases; every purpose is a read.
DISCOVERY_QUERIES: tuple[tuple[Step, str], ...] = (
    ("identity", "get my own chat user profile"),
    ("identity", "get my chat workspace info"),
    ("channels", "list chat channels I am a member of"),
    ("channels", "list my direct message conversations"),
    ("history", "fetch recent messages in a chat channel"),
    ("history", "fetch replies in a chat thread"),
    ("history", "search chat messages that mention me"),
)

_CHAT_WORDS = ("chat", "channel", "conversation", "direct message", "message", "thread", "workspace", "dm", "im")
_OTHER_WORDS = ("pull request", "commit", "repository", "issue", "ticket", "email", "mail", "calendar", "event",
                "invoice", "spreadsheet", "sheet", "page", "database", "file")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """This request's chat read tools. `found` is what the search queries returned (with the step of the
    first query that found each tool in `provenance`); `searched` is False without search results."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Step] = field(default_factory=dict)
    searched: bool = False

    @property
    def writes(self) -> frozenset[str]:
        return frozenset(t.name for t in (*self.listed, *self.found) if looks_like_write(t))

    @property
    def reads(self) -> tuple[McpTool, ...]:
        """Every chat read: found tools first, then listed ones; tools whose words name another kind of
        source are left out, and unclassified ones (a user profile read) are kept only next to at least
        one tool that says chat. Never a write, never a meta-tool."""
        pool = [t for t in _unique((*self.found, *self.listed)) if not looks_like_write(t)]
        if not any(is_chat(t) is True for t in pool):
            return ()  # no tool says chat: whatever the search returned is another kind of source
        return tuple(t for t in pool if is_chat(t) is not False)

    def for_step(self, step: Step) -> tuple[McpTool, ...]:
        """All chat reads, those found for this step first (the model picks among them)."""
        reads = self.reads
        own = [t for t in reads if self.provenance.get(t.name) == step]
        return (*own, *(t for t in reads if t not in own))


def is_chat(tool: McpTool) -> bool | None:
    """True for a chat tool, False for another kind of source, None when the words do not say."""
    for text in (_words(tool.name), _words(tool.description)):
        chat = any(_has(text, w) for w in _CHAT_WORDS)
        other = any(_has(text, w) for w in _OTHER_WORDS)
        if chat != other:
            return chat
    return None


def _has(text: str, phrase: str) -> bool:
    return any(f" {phrase}{suffix} " in text for suffix in ("", "s", "es"))


def _words(text: str) -> str:
    return f" {_NON_ALNUM.sub(' ', _CAMEL.sub(' ', text).casefold()).strip()} "


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_CHAT_TOOL
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", type(e).__name__)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    catalog = Catalog(listed=tuple(t for t in listed if t.name not in HIDDEN_META))
    if SEARCH_META in names:
        catalog = await _search(deps, catalog)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    if not catalog.reads:
        return None, prompts.NO_CHAT_TOOL
    log.info("chat read tools: %s", [t.name for t in catalog.reads])
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog) -> Catalog:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    provenance: dict[str, Step] = {}
    answered = 0
    for (step, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s", query, type(outcome).__name__)
            continue
        answered += 1
        found = (*found, *outcome)
        provenance = {**{t.name: step for t in outcome}, **provenance}  # first query wins
    if not answered:
        log.warning("every tool search failed; falling back to tools/list alone")
        return catalog
    return Catalog(listed=catalog.listed, found=_unique(found), provenance=MappingProxyType(provenance),
                   searched=True)


async def _ask(deps: TurnDeps, query: str) -> tuple[McpTool, ...]:
    result = await deps.gateway.call_tool(SEARCH_META, {"query": query, "limit": SEARCH_LIMIT}, deps.traceparent)
    if not isinstance(result, ToolOk) or result.is_error:
        raise McpGatewayError(f"search returned {type(result).__name__}")
    tools = tuple(t for t in (_tool(raw) for raw in _found(result)) if t is not None)
    return tuple(t for t in tools if is_chat(t) is not False)


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
