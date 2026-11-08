"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the flow title, so the
storage tools this agent needs may be missing from it. The agent asks the search meta-tool itself, once per
query, concurrently, on every request (the gateway needs this request's traceparent, so nothing is
cached). No LLM call is involved. With tool search off the meta-tool is not listed and `tools/list` alone
is used, each tool placed in a step by its own words.

Read-only: anything that reads as a write is never kept. Tools are found by capability phrases; no tool
name is written here.
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
import sources
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("doc_converter.discovery")

SEARCH_META = "nasiko_search_tools"
HIDDEN_META = frozenset({SEARCH_META, "nasiko_call_tool", "recover_compressed"})
SEARCH_LIMIT = 8

Step = Literal["find", "meta", "fetch"]
STEPS: tuple[Step, ...] = ("find", "meta", "fetch")

# (place, step, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[str, Step, str], ...] = (
    ("drive", "find", "search for a file by name in cloud drive"),
    ("drive", "meta", "get file metadata name size type"),
    ("drive", "fetch", "download file content"),
    ("drive", "fetch", "export document"),
    ("dropbox", "find", "search for a file in file storage"),
    ("dropbox", "meta", "get file metadata"),
    ("dropbox", "fetch", "download a file"),
)
WRITE_WORDS = frozenset({"create", "upload", "delete", "remove", "move", "copy", "share", "update", "write", "send",
                         "post", "rename", "add", "edit", "put", "trash", "restore", "empty", "insert", "patch",
                         "modify", "set", "replace", "save", "unshare", "revoke", "lock", "unlock"})
_STEP_WORDS: dict[Step, tuple[str, ...]] = {
    "find": ("search", "find", "list_files", "query"),
    "meta": ("metadata", "get_file", "file_info", "get_info"),
    "fetch": ("download", "export", "read_file", "file_content", "get_content"),
}


@dataclass(frozen=True)
class Catalog:
    """This request's read tools per (place, step), in rank order."""

    by_step: Mapping[tuple[str, Step], tuple[McpTool, ...]] = field(default_factory=dict)

    def tools(self, place: str | None, step: Step) -> tuple[McpTool, ...]:
        places = (place,) if place else sources.PLACES
        return _unique(t for p in places for t in self.by_step.get((p, step), ()))

    def has_any(self, place: str | None) -> bool:
        return any(self.tools(place, s) for s in STEPS)


def looks_like_write(tool: McpTool) -> bool:
    tokens = {t for t in tool.name.casefold().replace("-", "_").split("_") if t}
    if tokens & WRITE_WORDS:
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_WORDS)


async def discover(deps: TurnDeps, place: str | None) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the gateway path."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_STORAGE
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    real = tuple(t for t in listed if t.name not in HIDDEN_META and not looks_like_write(t))
    by_step = _by_words(real, place)
    if any(t.name == SEARCH_META for t in listed):
        by_step = await _search(deps, place, by_step)
    catalog = Catalog(MappingProxyType(by_step))
    log.info("storage tools: %s", {f"{p}/{s}": [t.name for t in v] for (p, s), v in by_step.items()})
    if not catalog.has_any(place):
        return None, prompts.NO_STORAGE
    return catalog, None


def _steps_named(tool: McpTool) -> set[Step]:
    text = tool.name.casefold()
    return {s for s, words in _STEP_WORDS.items() if any(w in text for w in words)}


def _fits_step(tool: McpTool, step: Step) -> bool:
    """A search hit stays in its query's step unless its name clearly says another step."""
    named = _steps_named(tool)
    return not named or step in named


def _by_words(tools: Iterable[McpTool], place: str | None) -> dict:
    out: dict[tuple[str, Step], tuple[McpTool, ...]] = {}
    for tool in tools:
        step = next((s for s in STEPS if s in _steps_named(tool)), None)
        where = sources.place_of_tool(tool.name, tool.description)
        if step and where and (place is None or where == place):
            out[(where, step)] = (*out.get((where, step), ()), tool)
    return out


async def _search(deps: TurnDeps, place: str | None, base: dict) -> dict:
    queries = [(p, s, q) for p, s, q in DISCOVERY_QUERIES if place is None or p == place]
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, _, q in queries), return_exceptions=True)
    found = dict(base)
    for (p, s, query), outcome in zip(queries, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        # A hit must name its place: other file stores (e.g. the platform's own artifact store) answer the
        # same capability queries and would be searched for a file that cannot be there.
        kept = tuple(t for t in outcome if not looks_like_write(t) and _fits_step(t, s)
                     and sources.place_of_tool(t.name, t.description) == p)
        found[(p, s)] = _unique((*kept, *found.get((p, s), ())))
    return found


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
