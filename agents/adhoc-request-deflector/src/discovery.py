"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name. Adapted from the MCP kit pattern; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first
message, so the tools each step needs may be missing from it. The agent asks the search meta-tool
itself, once per query, concurrently (this request's traceparent, so nothing is cached). No LLM call.
With tool search off the meta-tool is not listed and `tools/list` alone is used.

Tools are sorted into roles by the words in their name (else their description). Meta-tools never enter
the catalog, the model is never offered a tool, and only the post role may be a write. No provider or
tool is named here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import prompts
import send_guard
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("deflector.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

Step = Literal["index", "requests", "reply"]
Role = Literal["sheet_search", "sheet_read", "history", "channels", "post"]

# (step, query). Provider-neutral capability phrases.
DISCOVERY_QUERIES: tuple[tuple[Step, str], ...] = (
    ("index", "search spreadsheets by title"),
    ("index", "read the values of a spreadsheet range"),
    ("requests", "list chat channels I am a member of"),
    ("requests", "fetch recent messages in a chat channel"),
    ("requests", "fetch replies in a chat thread"),
    ("reply", "post a chat message in a thread"),
)

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_SHEET = ("spreadsheet", "sheet", "workbook")
_FILES = ("file", "drive")
_CHAT = ("chat", "channel", "conversation", "message", "thread", "dm")
_NOT_A_POST = ("schedule", "scheduled", "ephemeral", "delete", "update", "edit", "me", "reminder", "draft")


def _words(text: str) -> tuple[str, ...]:
    return tuple(_NON_ALNUM.sub(" ", _CAMEL.sub(" ", text).casefold()).split())


def _has(words: Sequence[str], *stems: str) -> bool:
    return any(w.startswith(s) for w in words for s in stems)


def _contains(words: Sequence[str], *parts: str) -> bool:
    return any(p in w for w in words for p in parts)


def _sheet_search(w: Sequence[str]) -> int:
    finds = _has(w, "search", "find")
    if finds and _contains(w, *_SHEET):
        return 3
    if finds and _has(w, *_FILES):
        return 1
    return 0


def _sheet_read(w: Sequence[str]) -> int:
    if not _contains(w, *_SHEET) or _has(w, "search", "find", "create", "update", "append", "clear", "delete"):
        return 0
    if _has(w, "value", "range", "batch"):
        return 3
    return 1 if _has(w, "get", "read", "fetch") else 0


def _history(w: Sequence[str]) -> int:
    if _contains(w, *_SHEET) or not _has(w, "channel", "conversation", "message"):
        return 0
    if _has(w, "histor"):
        return 3
    return 1 if _has(w, "fetch", "read", "recent") and not _has(w, "search", "repl", "link", "permalink") else 0


def _channels(w: Sequence[str]) -> int:
    return 2 if _has(w, "list") and _has(w, "channel", "conversation") and not _contains(w, *_SHEET) else 0


def _post(w: Sequence[str]) -> int:
    if not _has(w, *_CHAT) or any(x in w for x in _NOT_A_POST):
        return 0
    return 2 if _has(w, "post", "send", "reply") and _has(w, "message", "chat", "repl") else 0


_ROLES: Mapping[Role, Callable[[Sequence[str]], int]] = {
    "sheet_search": _sheet_search, "sheet_read": _sheet_read, "history": _history, "channels": _channels,
    "post": _post,
}


def role_score(tool: McpTool, role: Role) -> int:
    """How well a tool fits a role: by its name, else by its description."""
    if (role == "post") != send_guard.is_write_tool(tool.name):
        return 0
    for text in (tool.name, tool.description):
        score = _ROLES[role](_words(text))
        if score:
            return score
    return 0


@dataclass(frozen=True)
class Catalog:
    tools: tuple[McpTool, ...]

    def role(self, role: Role) -> McpTool | None:
        scored = [(role_score(t, role), n, t) for n, t in enumerate(self.tools)]
        best = max((s for s in scored if s[0] > 0), key=lambda s: (s[0], -s[1]), default=None)
        return best[2] if best else None

    def source_reads(self, roles: Iterable[Role]) -> tuple[str, ...]:
        return tuple(t.name for r in roles if (t := self.role(r)) is not None)


async def discover(deps: TurnDeps, steps: Sequence[Step]) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn without an LLM call."""
    if deps.gateway is None:
        return Catalog(()), None
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", type(e).__name__)
        return None, prompts.GATEWAY_UNREACHABLE
    found: tuple[McpTool, ...] = ()
    if SEARCH_META in {t.name for t in listed}:
        found = await _search(deps, steps)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    tools = _unique((*(t for t in listed if t.name not in HIDDEN_META), *found))
    log.info("tools: %s", [t.name for t in tools])
    return Catalog(tools), None


async def _search(deps: TurnDeps, steps: Sequence[Step]) -> tuple[McpTool, ...]:
    queries = [q for s, q in DISCOVERY_QUERIES if s in steps]
    outcomes = await asyncio.gather(*(_ask(deps, q) for q in queries), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    for query, outcome in zip(queries, outcomes, strict=True):
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
