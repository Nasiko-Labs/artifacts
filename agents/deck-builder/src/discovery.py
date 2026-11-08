"""Per-request tool discovery for the upload: `tools/list`, plus the gateway's search meta-tool asked a fixed
set of capability queries (no LLM), concurrently, merged by name. Run only when an upload is asked for.
No provider or tool is named here; the found tools are called directly by their real names."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("deck_builder.discovery")

SEARCH_META = "nasiko_search_tools"
HIDDEN_META = frozenset({SEARCH_META, "nasiko_call_tool", "recover_compressed", "nasiko_memory_search"})
SEARCH_LIMIT = 8
DISCOVERY_QUERIES = (
    "upload a file to cloud drive",
    "find a folder by name in cloud drive",
    "create a file in cloud drive",
)
NO_GATEWAY = "No drive is connected to this agent, so I can't upload the deck."
GATEWAY_DOWN = "I couldn't reach the tool gateway just now, so I can't upload the deck."


async def discover(deps: TurnDeps) -> tuple[tuple[McpTool, ...], str | None]:
    """This request's tools, or the note that explains why there are none (the deck is still delivered)."""
    if deps.gateway is None or not deps.traceparent:
        return (), NO_GATEWAY
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", type(e).__name__)
        return (), GATEWAY_DOWN
    names = {t.name for t in listed}
    tools = tuple(t for t in listed if t.name not in HIDDEN_META)
    if SEARCH_META in names:
        tools = _unique((*tools, *await _search_all(deps)))
    log.info("upload tools seen: %s", [t.name for t in tools])
    return tools, None if tools else NO_GATEWAY


async def _search_all(deps: TurnDeps) -> tuple[McpTool, ...]:
    outcomes = await asyncio.gather(*(_ask(deps, q) for q in DISCOVERY_QUERIES), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    for query, outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s", query, type(outcome).__name__)
            continue
        found = (*found, *outcome)
    return found


async def _ask(deps: TurnDeps, query: str) -> tuple[McpTool, ...]:
    result = await deps.gateway.call_tool(SEARCH_META, {"query": query, "limit": SEARCH_LIMIT}, deps.traceparent)
    if not isinstance(result, ToolOk) or result.is_error:
        raise McpGatewayError(f"search returned {type(result).__name__}")
    structured = result.raw.get("structuredContent")
    payload: Any = structured if isinstance(structured, Mapping) else _json(result.text)
    raw_tools = payload.get("tools") if isinstance(payload, Mapping) else None
    if not isinstance(raw_tools, list):
        raise McpGatewayError("search result has no `tools` list")
    return tuple(t for t in (_tool(raw) for raw in raw_tools) if t is not None)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _tool(raw: Any) -> McpTool | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str) or raw["name"] in HIDDEN_META:
        return None
    schema = raw.get("inputSchema")
    return McpTool(name=raw["name"], description=str(raw.get("description") or ""),
                   input_schema=MappingProxyType(dict(schema) if isinstance(schema, Mapping) else {}))


def _unique(tools: Iterable[McpTool]) -> tuple[McpTool, ...]:
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
