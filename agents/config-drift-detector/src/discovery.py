"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked fixed capability
queries (concurrently, limit 8, this request's traceparent, no LLM call), merged by name. Adapted from
the promise-tracker pattern; owned here.

With tool search on, `tools/list` holds only a handful of tools ranked on the first message, so the code
host reads may be missing from it. With search off the meta-tool is not listed and `tools/list` alone is
used. The model never sees the meta-tools and never sees a write tool. Only tools recognised as code
host reads (by name or description) are offered. No provider or tool is named here.
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

from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from readonly import CALL_META, looks_like_write

log = logging.getLogger("drift.discovery")

SEARCH_META = "nasiko_search_tools"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8

# (step, query). Provider-neutral capability phrases; every purpose is a read.
DISCOVERY_QUERIES: tuple[tuple[str, str], ...] = (
    ("iac", "get file contents at a ref"),
    ("iac", "search code in a repository by text"),
    ("iac", "list files in a directory of a repository"),
    ("history", "list commits that changed a file path"),
    ("history", "get a commit with author and message"),
)
# A code-host read names the repository side in its name, or says so in its description; generic file
# tools (a workspace or doc store) are not offered.
_CODE_WORDS = ("repo", "repos", "repository", "repositories", "commit", "commits", "code", "tree", "blob", "branch",
               "branches", "git")
_DESCRIPTION_WORDS = ("repository", "repositories", "commit", "commits", "git")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

NO_GATEWAY, UNREACHABLE, NO_TOOLS = "no_gateway", "unreachable", "no_tools"


@dataclass(frozen=True)
class Catalog:
    tools: tuple[McpTool, ...]          # code-host reads offered to the model
    writes: frozenset[str] = frozenset()


def is_code_host_read(tool: McpTool) -> bool:
    if looks_like_write(tool):
        return False
    name = _words(tool.name)
    description = _words(tool.description[:200])
    return any(f" {w} " in name for w in _CODE_WORDS) or any(f" {w} " in description for w in _DESCRIPTION_WORDS)


def _words(text: str) -> str:
    return f" {_NON_ALNUM.sub(' ', _CAMEL.sub(' ', text).casefold()).strip()} "


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str]:
    """This request's code-host catalog, or why there is none (`no_gateway`, `unreachable`, `no_tools`)."""
    if deps.gateway is None:
        return None, NO_GATEWAY
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", type(e).__name__)
        return None, UNREACHABLE
    found: tuple[McpTool, ...] = ()
    if any(t.name == SEARCH_META for t in listed):
        found = await _search(deps)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    pool = _unique((*found, *(t for t in listed if t.name not in HIDDEN_META)))
    writes = frozenset(t.name for t in pool if looks_like_write(t))
    reads = tuple(t for t in pool if is_code_host_read(t))
    log.info("code host reads offered: %s", [t.name for t in reads])
    return (Catalog(reads, writes), "") if reads else (None, NO_TOOLS)


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
