"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title (the
user's first message), so the read tools this agent needs may be missing from it. The agent asks the
search meta-tool itself, once per query, concurrently, on every request (the gateway needs this
request's traceparent, so nothing is cached). No LLM call is involved. With tool search off the
meta-tool is not listed and `tools/list` alone is used.

This agent is read-only: no tool whose name or description reads as a write is ever offered to the
model, whatever the search returned. The model never sees the meta-tools: found tools are called
directly by their real names. No provider or tool is named here.

(Adapted from the hardened kit; owned by this agent. There is no send guard: nothing is ever written.)
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

log = logging.getLogger("deal_reconciler.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META})  # never offered to the model
SEARCH_LIMIT = 8

Source = Literal["documents", "payments"]

# (source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Source, str], ...] = (
    ("documents", "search documents by name"),
    ("documents", "search files containing text"),
    ("documents", "read the text of a document"),
    ("documents", "export a document as plain text"),
    ("payments", "search customers by name or email"),
    ("payments", "list subscriptions for a customer"),
    ("payments", "retrieve a subscription with items and discounts"),
    ("payments", "list subscription schedules for a customer"),
    ("payments", "retrieve a price"),
    ("payments", "retrieve a coupon"),
)

# Verb tokens that mark a tool as one that changes state, matched against the tool name split into words,
# so read tools (`fetch`, `list`, `search`, `get`, `retrieve`, `export`) never match.
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove", "trash",
    "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "join", "leave", "rename", "upload",
    "insert", "write", "publish", "schedule", "share", "submit", "import", "cancel", "complete", "close", "assign",
    "approve", "transfer", "notify", "copy", "append", "replace", "resume", "pause", "release", "migrate",
    # payments writes
    "void", "finalize", "pay", "refund", "capture", "confirm", "reverse", "attach", "detach", "apply", "charge",
    "redeem", "issue",
})

# Words that place a tool in a source; the name is checked before the description.
_SOURCE_WORDS: dict[Source, tuple[str, ...]] = {
    "documents": ("document", "docs", "file", "folder"),
    "payments": ("payment", "invoice", "subscription", "customer", "price", "billing", "coupon", "discount"),
}
# The platform's own artifact store and memory tools come back from every search; they hold no contract
# and no billing data, so they are never offered.
_NOT_SOURCE_WORDS = frozenset({"memory", "workspace", "artifact", "artifacts"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def name_tokens(tool_name: str) -> tuple[str, ...]:
    """`c_1a2b__DOCS_exportText` → ("c", "1a2b", "docs", "export", "text")."""
    spaced = _CAMEL_BOUNDARY.sub("_", tool_name)
    return tuple(t.lower() for t in _NON_ALNUM.split(spaced) if t)


def is_write_tool(tool_name: str) -> bool:
    tokens = name_tokens(tool_name)
    return any(token in WRITE_VERBS for token in tokens) or {"chat", "message"} <= set(tokens)


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Creates ...")."""
    if is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    """The one source whose words the name (else the description) carries; None when none or several."""
    if _NOT_SOURCE_WORDS & set(name_tokens(tool.name)):
        return None
    for text in (tool.name.casefold(), tool.description.casefold()):
        hits = [source for source, words in _SOURCE_WORDS.items() if any(w in text for w in words)]
        if len(hits) == 1:
            return hits[0]
    return None


@dataclass(frozen=True)
class Catalog:
    """This request's read tools: `tools/list` plus what the searches found, deduplicated, writes removed."""

    tools: tuple[McpTool, ...]
    recover: McpTool | None = None
    searched: bool = False

    def for_source(self, source: Source) -> tuple[McpTool, ...]:
        return tuple(t for t in self.tools if classify(t) == source)


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_PAYMENTS_TOOL
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    recover = next((t for t in listed if t.name == RECOVER_META), None)
    real = tuple(t for t in listed if t.name not in HIDDEN_META and t.name != RECOVER_META)
    found: tuple[McpTool, ...] = ()
    searched = False
    if SEARCH_META in names:
        found, searched = await _search(deps)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    tools = tuple(t for t in _unique((*real, *found)) if not looks_like_write(t))
    log.info("read tools this turn: %s", [t.name for t in tools])
    return Catalog(tools, recover, searched), None


async def _search(deps: TurnDeps) -> tuple[tuple[McpTool, ...], bool]:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    answered = 0
    for (_, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found = (*found, *outcome)
    if not answered:
        log.warning("every tool search failed; falling back to tools/list alone")
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
