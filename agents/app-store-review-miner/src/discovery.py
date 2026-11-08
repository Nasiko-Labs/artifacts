"""Per-request tool discovery for the optional review tool: `tools/list`, plus the gateway's search
meta-tool asked the fixed capability queries (concurrently, no LLM, no cache), merged by name.

Only read tools that are about app reviews are ever offered; anything that reads as a write is left out,
and the meta-tools are never offered to the model. No provider or tool is named here."""

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

log = logging.getLogger("review_miner.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8
QUERIES = ("get recent app store reviews for an app", "get google play reviews for an app by version",
           "search app reviews by text")
WRITE_VERBS = frozenset({
    "send", "post", "reply", "respond", "forward", "create", "update", "modify", "patch", "edit", "delete",
    "remove", "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "rename",
    "upload", "insert", "write", "publish", "schedule", "share", "submit", "import", "cancel", "close",
    "assign", "approve", "transfer", "notify", "associate",
})
_REVIEW_WORDS = ("review", "rating")
_NOT_APP_REVIEWS = ("pull request", "pull_request", "pullrequest", "code review", "merge request", "diff")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()

    def read_tools(self) -> tuple[McpTool, ...]:
        pool = self.found or self.listed
        return tuple(t for t in pool if is_review_tool(t) and not looks_like_write(t))


def name_tokens(tool_name: str) -> tuple[str, ...]:
    return tuple(t.lower() for t in _NON_ALNUM.split(_CAMEL.sub("_", tool_name)) if t)


def looks_like_write(tool: McpTool) -> bool:
    if any(token in WRITE_VERBS for token in name_tokens(tool.name)):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    return bool(first) and bool({first, first[:-1], first[:-2], first[:-3] + "y"} & WRITE_VERBS)


def is_review_tool(tool: McpTool) -> bool:
    text = f"{tool.name} {tool.description}".casefold()
    return any(w in text for w in _REVIEW_WORDS) and not any(w in text for w in _NOT_APP_REVIEWS)


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
