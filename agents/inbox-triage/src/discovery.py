"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title (the
user's first message), so the email tools this agent needs may be missing from it. The agent asks the
search meta-tool itself, once per query, concurrently, on every request (the gateway needs this
request's traceparent, so nothing is cached). No LLM call is involved. With tool search off the
meta-tool is not listed and `tools/list` alone is used.

The model never sees the meta-tools, and this read-only agent never offers a tool that reads as a write.
No provider or tool is named here.

(Copied in once from the hardened kit; owned by this agent. The queries are this agent's.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import prompts
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("inbox_triage.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})  # never offered to the model
SEARCH_LIMIT = 8

# Provider-neutral capability phrases (plan section 7); tune here.
DISCOVERY_QUERIES: tuple[str, ...] = (
    "search my email messages with a query",
    "list recent emails in my inbox",
    "get my email account profile",
    "fetch an email thread with all its messages",
    "get an email message with headers and labels",
)

# Verb tokens that mark a tool as one that changes state; matched against the tool name split into words.
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove",
    "trash", "archive", "move", "add", "set", "mark", "pin", "react", "invite", "rename", "upload", "insert",
    "write", "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "label", "draft",
    "unsubscribe", "spam", "untrash", "batch", "keep", "promote",
})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
# "mail" covers email/mailbox. "inbox", "message" and "thread" alone also match file and chat tools
# (a workspace "inbox" is staged uploads), so they do not make a tool an email source.
_EMAIL_WORDS = ("mail",)


def name_tokens(name: str) -> set[str]:
    return {t.casefold() for t in _NON_ALNUM.split(_CAMEL.sub(" ", name)) if t}


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Sends ...")."""
    if name_tokens(tool.name) & WRITE_VERBS:
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


def is_email_tool(tool: McpTool) -> bool:
    text = f"{tool.name} {tool.description}".casefold()
    return any(w in text for w in _EMAIL_WORDS)


@dataclass(frozen=True)
class Catalog:
    """This request's read tools: `tools/list` and search results merged by name, writes and meta-tools removed."""

    tools: tuple[McpTool, ...]
    searched: bool = False

    def by_name(self, name: str) -> McpTool | None:
        return next((t for t in self.tools if t.name == name), None)


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's read catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.PASTE_PROMPT
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    found: tuple[McpTool, ...] = ()
    searched = False
    if SEARCH_META in names:
        found, searched = await _search(deps)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    pool = _unique((*found, *listed))
    tools = tuple(t for t in pool if t.name not in HIDDEN_META and not looks_like_write(t) and is_email_tool(t))
    log.info("read tools: %s", [t.name for t in tools])
    if not tools:
        return None, prompts.PASTE_PROMPT
    return Catalog(tools=tools, searched=searched), None


async def _search(deps: TurnDeps) -> tuple[tuple[McpTool, ...], bool]:
    outcomes = await asyncio.gather(*(_ask(deps, q) for q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    answered = 0
    for query, outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found = (*found, *outcome)
    return found, answered > 0


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
    """First occurrence of each name wins, in order."""
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
