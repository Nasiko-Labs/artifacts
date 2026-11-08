"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked the fixed capability
queries (concurrently, no LLM, no cache), merged by name.

Two kinds of read tool are offered, each only to its own step: issue-tracker reads (the roadmap step) and
page readers (the optional fetch). Anything that reads as a write is left out, and the meta-tools are never
offered to the model. No provider or tool is named here."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("change_watcher.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8
TRACKER_QUERIES = ("search issues by text", "list issues of a project or roadmap with state",
                   "get an issue with its description and url")
FETCH_QUERIES = ("fetch a web page as text", "scrape a page to markdown")
QUERIES = (*TRACKER_QUERIES, *FETCH_QUERIES)
WRITE_VERBS = frozenset({
    "send", "post", "reply", "respond", "forward", "create", "update", "modify", "patch", "edit", "delete",
    "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "rename",
    "upload", "insert", "write", "publish", "schedule", "share", "submit", "import", "cancel", "close",
    "assign", "approve", "transfer", "notify", "associate",
})
_TRACKER_WORDS = ("issue", "ticket", "roadmap", "project")
_PAGE_WORDS = ("scrape", "crawl", "web page", "webpage", "url", "website", "markdown")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()

    def _reads(self) -> tuple[McpTool, ...]:
        return tuple(t for t in (self.found or self.listed) if not looks_like_write(t))

    def tracker_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self._reads() if is_tracker_tool(t))

    def page_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self._reads() if is_page_tool(t) and not is_tracker_tool(t))


def name_tokens(tool_name: str) -> tuple[str, ...]:
    return tuple(t.lower() for t in _NON_ALNUM.split(_CAMEL.sub("_", tool_name)) if t)


def looks_like_write(tool: McpTool) -> bool:
    if any(token in WRITE_VERBS for token in name_tokens(tool.name)):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    return bool(first) and bool({first, first[:-1], first[:-2], first[:-3] + "y"} & WRITE_VERBS)


def _text(tool: McpTool) -> str:
    return f"{' '.join(name_tokens(tool.name))} {tool.description}".casefold()


def is_tracker_tool(tool: McpTool) -> bool:
    return any(w in _text(tool) for w in _TRACKER_WORDS)


def is_page_tool(tool: McpTool) -> bool:
    return any(w in _text(tool) for w in _PAGE_WORDS)


async def discover(deps: TurnDeps) -> Catalog:
    """This request's catalog. Raises McpGatewayError when `tools/list` fails."""
    listed = await deps.gateway.list_tools(deps.traceparent)
    real = tuple(t for t in listed if t.name not in HIDDEN_META)
    catalog = Catalog(listed=real)
    if any(t.name == SEARCH_META for t in listed):
        catalog = Catalog(listed=real, found=await _search(deps))
    log.info("listed %s; found %s", [t.name for t in catalog.listed], [t.name for t in catalog.found])
    return catalog


async def _search(deps: TurnDeps) -> tuple[McpTool, ...]:
    outcomes = await asyncio.gather(*(_ask(deps, q) for q in QUERIES), return_exceptions=True)
    for query, outcome in zip(QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
    return _unique(t for o in outcomes if not isinstance(o, BaseException) for t in o)


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
