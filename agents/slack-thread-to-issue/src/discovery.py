"""Per-request tool discovery (copied in from the kit once; owned and adapted here): `tools/list`,
plus the gateway's search meta-tool asked a fixed set of capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first message,
so the chat and tracker tools this agent needs may be missing from it. The agent asks the search
meta-tool itself, once per query, concurrently, on every request (the gateway needs this request's
traceparent, so nothing is cached). No LLM call is involved. With tool search off the meta-tool is not
listed and `tools/list` alone is used.

The model never sees the meta-tools: found tools are called by their real names, so the guards always see
the real call. No provider or tool is named here; a tracker "family" is the connector prefix of its tools.
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

log = logging.getLogger("thread_to_issue.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})  # never offered to the model
SEARCH_LIMIT = 8

Purpose = Literal["scan", "tracker_read", "create", "link_back"]
Source = Literal["chat", "tracker"]
_SOURCE_OF_PURPOSE: Mapping[str, Source] = {"scan": "chat", "link_back": "chat", "tracker_read": "tracker",
                                            "create": "tracker"}

# (purpose, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Purpose, str], ...] = (
    ("scan", "list chat channels I am a member of"),
    ("scan", "fetch recent messages in a chat channel"),
    ("scan", "fetch replies in a chat thread"),
    ("scan", "get a permanent link to a chat message"),
    ("tracker_read", "list teams and projects in the issue tracker"),
    ("tracker_read", "list labels in the issue tracker"),
    ("tracker_read", "search issues by text"),
    ("create", "create an issue in the issue tracker"),
    ("link_back", "post a chat message in a thread"),
)

_CHAT_WORDS = ("chat", "channel", "conversation")
_TRACKER_WORDS = ("issue", "ticket", "tracker", "project", "label", "team", "board", "sprint", "cycle", "epic")
_CREATE_OBJECTS = ("issue", "ticket", "task", "story", "bug")
_POST_VERBS = ("post", "send", "reply")
_NOT_A_PLAIN_POST = frozenset({"schedule", "scheduled", "ephemeral", "me", "delete", "update", "draft", "reminder",
                               "reaction", "react", "pin", "unpin", "edit", "file", "upload"})


@dataclass(frozen=True)
class Catalog:
    """This request's tools: `listed` from `tools/list` (meta-tools removed), `found` by purpose from search."""

    listed: tuple[McpTool, ...]
    found: Mapping[str, tuple[McpTool, ...]] = field(default_factory=dict)
    provenance: Mapping[str, Source] = field(default_factory=dict)

    @property
    def tools(self) -> tuple[McpTool, ...]:
        return _unique((*self.listed, *(t for tools in self.found.values() for t in tools)))

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)

    def _pool(self, purpose: Purpose) -> tuple[McpTool, ...]:
        source = _SOURCE_OF_PURPOSE[purpose]
        pool = _unique((*self.found.get(purpose, ()), *self.tools))
        return tuple(t for t in pool if self.source_of(t) == source)

    def chat_read_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self._pool("scan") if not looks_like_write(t))

    def families(self) -> tuple[str, ...]:
        """Tracker families (connector prefixes) that have at least one tracker tool, in order seen."""
        tracker = (*self._pool("tracker_read"), *self._pool("create"))
        return tuple(dict.fromkeys(family_of(t) for t in tracker))

    def tracker_read_tools(self, family: str | None) -> tuple[McpTool, ...]:
        return tuple(t for t in self._pool("tracker_read")
                     if not looks_like_write(t) and (family is None or family_of(t) == family))

    def create_tool(self, family: str | None) -> McpTool | None:
        writes = [t for t in self._pool("create") if send_guard.is_write_tool(t.name)
                  and (family is None or family_of(t) == family)]
        creates = [t for t in writes if "create" in send_guard.name_tokens(t.name)]
        best = [t for t in creates if any(o in send_guard.name_tokens(t.name) for o in _CREATE_OBJECTS)]
        return next(iter(best or creates), None)

    def post_tool(self) -> McpTool | None:
        """The tool that posts a plain chat message (a thread reply), never a scheduled/ephemeral/edit one."""
        writes = [t for t in self._pool("link_back") if send_guard.is_write_tool(t.name)
                  and not set(send_guard.name_tokens(t.name)) & _NOT_A_PLAIN_POST]
        replies = [t for t in writes if "reply" in send_guard.name_tokens(t.name)]
        posts = [t for t in writes if any(v in send_guard.name_tokens(t.name) for v in _POST_VERBS)]
        return next(iter(replies or posts), None)


def family_of(tool: McpTool) -> str:
    """The connector prefix of a gateway tool name (`c1a2__create_issue` -> `c1a2`)."""
    return tool.name.split("__", 1)[0] if "__" in tool.name else ""


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Creates ...")."""
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    for text in (tool.name.casefold(), tool.description.casefold()):
        chat = any(w in text for w in _CHAT_WORDS)
        tracker = any(w in text for w in _TRACKER_WORDS)
        if chat != tracker:
            return "chat" if chat else "tracker"
    return None


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_CHAT_TOOL
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    catalog = Catalog(listed=tuple(t for t in listed if t.name not in HIDDEN_META))
    if SEARCH_META in names:
        catalog = await _search(deps, catalog)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    log.info("tools listed: %s; found by search: %s", [t.name for t in catalog.listed],
             {k: [t.name for t in v] for k, v in catalog.found.items()})
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog) -> Catalog:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: dict[str, tuple[McpTool, ...]] = {}
    provenance: dict[str, Source] = {}
    for (purpose, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        found = {**found, purpose: _unique((*found.get(purpose, ()), *outcome))}
        provenance = {**{t.name: _SOURCE_OF_PURPOSE[purpose] for t in outcome}, **provenance}
    return Catalog(listed=catalog.listed, found=MappingProxyType(found), provenance=MappingProxyType(provenance))


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
