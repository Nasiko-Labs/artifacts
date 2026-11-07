"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name. Adapted from the MCP kit pattern; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first
message, so the reads each step needs may be missing from it. The agent asks the search meta-tool
itself, once per query, concurrently (this request's traceparent, so nothing is cached). No LLM call.
With tool search off the meta-tool is not listed and `tools/list` alone is used.

Meta-tools and write tools never enter the catalog. No provider or tool is named here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import prompts
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from readonly import CALL_META, looks_like_write

log = logging.getLogger("dedup.discovery")

SEARCH_META = "nasiko_search_tools"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

Step = Literal["search", "thread"]
Role = Literal["search", "replies", "history", "channels"]

# (step, query). Provider-neutral capability phrases; every purpose is a read.
DISCOVERY_QUERIES: tuple[tuple[Step, str], ...] = (
    ("search", "search chat messages containing a phrase"),
    ("search", "search chat messages in a channel"),
    ("search", "list chat channels I am a member of"),
    ("search", "fetch recent messages in a chat channel"),
    ("thread", "fetch replies in a chat thread"),
    ("thread", "get a permanent link to a chat message"),
    ("thread", "get a chat user profile"),
)

_CHAT_WORDS = ("chat", "channel", "conversation", "direct message", "message", "thread", "workspace", "dm", "im")
_OTHER_WORDS = ("pull request", "commit", "repository", "issue", "ticket", "email", "mail", "calendar", "event",
                "invoice", "spreadsheet", "sheet", "page", "database", "file")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """This request's chat read tools, by role."""

    reads: tuple[McpTool, ...]

    def role(self, role: Role) -> McpTool | None:
        return next((t for t in self.reads if role_of(t) == role), None)


def is_chat(tool: McpTool) -> bool | None:
    """True for a chat tool, False for another kind of source, None when the words do not say."""
    for text in (_words(tool.name), _words(tool.description)):
        chat = any(_has(text, w) for w in _CHAT_WORDS)
        other = any(_has(text, w) for w in _OTHER_WORDS)
        if chat != other:
            return chat
    return None


def role_of(tool: McpTool) -> Role | None:
    """What a chat read tool does, from its name (else its description)."""
    for text in (_words(tool.name), _words(tool.description)):
        role = _role(text)
        if role is not None:
            return role
    return None


def _role(text: str) -> Role | None:
    if _has(text, "search"):
        return "search" if _has(text, "message") or not _has(text, "channel") else None
    if _has(text, "replies") or _has(text, "reply") or (_has(text, "thread") and not _has(text, "list")):
        return "replies"
    if _has(text, "history") or (_has(text, "message") and not _has(text, "link") and not _has(text, "permalink")):
        return "history"
    if _has(text, "list") and (_has(text, "channel") or _has(text, "conversation")):
        return "channels"
    return None


def _has(text: str, phrase: str) -> bool:
    return any(f" {phrase}{suffix} " in text for suffix in ("", "s", "es"))


def _words(text: str) -> str:
    return f" {_NON_ALNUM.sub(' ', _CAMEL.sub(' ', text).casefold()).strip()} "


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if deps.gateway is None:
        return None, prompts.NO_CHAT_TOOL
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", type(e).__name__)
        return None, prompts.GATEWAY_UNREACHABLE
    found: tuple[McpTool, ...] = ()
    if SEARCH_META in {t.name for t in listed}:
        found = await _search(deps)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    reads = _chat_reads((*found, *(t for t in listed if t.name not in HIDDEN_META)))
    if not reads:
        return None, prompts.NO_CHAT_TOOL
    log.info("chat read tools: %s", [t.name for t in reads])
    return Catalog(reads), None


def _chat_reads(tools: Iterable[McpTool]) -> tuple[McpTool, ...]:
    """Chat reads only: never a write, never a tool whose words name another kind of source; and nothing
    at all unless at least one tool positively says chat."""
    pool = [t for t in _unique(tools) if not looks_like_write(t)]
    if not any(is_chat(t) is True for t in pool):
        return ()
    return tuple(t for t in pool if is_chat(t) is not False)


async def _search(deps: TurnDeps) -> tuple[McpTool, ...]:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    for (_, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s", query, type(outcome).__name__)
            continue
        found = (*found, *outcome)
    return _unique(found)


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
