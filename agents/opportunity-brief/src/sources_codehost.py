"""Code-host repositories through the Nasiko MCP gateway, driven by code (no LLM call): discover read tools
with fixed capability queries, run one repository search for the topic, then read details/README for the
user's named repositories and the top results. At most 6 tool calls, 60 s each; tool output is untrusted
and is only parsed into evidence items. Any failure becomes a coverage line; the brief goes on."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

import coverage
import repos
from deps import BudgetSpent, TurnDeps
from discovery import Catalog, discover
from fence import FencedGateway, original_text
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    McpTool,
    ToolOk,
)
from store import Item
from tool_health import ToolHealth, failure_text, unavailable_reason

log = logging.getLogger("opportunity_brief.codehost")

CALL_TIMEOUT_SECS = 60.0
TOP_READMES = 3


@dataclass(frozen=True)
class CodeResult:
    items: tuple[Item, ...] = ()
    notes: tuple[str, ...] = ()
    read: bool = False
    failed: bool = False


class _Stop(Exception):
    def __init__(self, state: str, reason: str = ""):
        super().__init__(state)
        self.state, self.reason = state, reason


def pick_search(catalog: Catalog) -> McpTool | None:
    return next((t for t in catalog.tools if _mentions(t, "repo") and _mentions(t, "search")
                 and repos.search_args(t.input_schema, "x") is not None), None)


def pick_details(catalog: Catalog) -> McpTool | None:
    return next((t for t in catalog.tools if _mentions(t, "repo") and not _mentions(t, "search")
                 and not _mentions(t, "readme") and repos.repo_args(t.input_schema, "o/n") is not None), None)


def pick_readme(catalog: Catalog) -> McpTool | None:
    return next((t for t in catalog.tools if _mentions(t, "readme")
                 and repos.repo_args(t.input_schema, "o/n") is not None), None)


def _mentions(tool: McpTool, word: str) -> bool:
    return word in tool.name.casefold() or word in tool.description.casefold()[:200]


async def collect(deps: TurnDeps, topic: str | None, named: tuple[str, ...]) -> CodeResult:
    if not topic and not named:
        return CodeResult()
    catalog, failure = await discover(deps)
    if catalog is None:
        return CodeResult(notes=(coverage.code_host_line(failure or "missing"),), failed=True)
    search, details, readme = pick_search(catalog), pick_details(catalog), pick_readme(catalog)
    if search is None and not (details and named):
        return CodeResult(notes=(coverage.code_host_line("no_search"),), failed=True)
    caller = ToolHealth(FencedGateway(deps.gateway))
    try:
        found = await _search(deps, caller, search, topic) if search and topic else []
        found = await _named(deps, caller, details, named, found)
        found = await _readmes(deps, caller, readme, found)
    except _Stop as stop:
        return CodeResult(notes=(coverage.code_host_line(stop.state, stop.reason),), failed=True)
    if not found:
        return CodeResult(notes=(coverage.code_host_line("empty"),), read=True)
    return CodeResult(tuple(found), read=True)


async def _call(deps: TurnDeps, caller: ToolHealth, tool: McpTool, args: dict) -> object | None:
    """Parsed JSON (or the raw text) of one call; None when that call failed in a recoverable way."""
    try:
        deps.budget.take("gateway")
    except BudgetSpent:
        return None
    try:
        result = await asyncio.wait_for(caller.call_tool(tool.name, args, deps.traceparent), CALL_TIMEOUT_SECS)
    except (McpGatewayError, TimeoutError) as e:
        log.warning("code host call %s failed: %s", tool.name, type(e).__name__)
        raise _Stop("unreachable") from e
    if isinstance(result, AuthorizationRequired):
        raise _Stop("auth_required")
    if isinstance(result, ApprovalPending):
        raise _Stop("approval")
    text = failure_text(result)
    if text is not None:
        reason = unavailable_reason(text)
        if reason:
            raise _Stop("unavailable", reason)
        return None
    raw = original_text(result) if isinstance(result, ToolOk) else ""
    parsed = repos.parse_json(raw)
    return parsed if parsed is not None else raw


async def _search(deps: TurnDeps, caller: ToolHealth, tool: McpTool, topic: str) -> list[Item]:
    payload = await _call(deps, caller, tool, repos.search_args(tool.input_schema, topic) or {})
    return repos.records(payload) if payload is not None else []


async def _named(deps: TurnDeps, caller: ToolHealth, reader: McpTool | None, named: tuple[str, ...],
                 found: list[Item]) -> list[Item]:
    known = {i.title.casefold() for i in found}
    out = list(found)
    for full in named:
        if reader is None or full.casefold() in known:
            continue
        payload = await _call(deps, caller, reader, repos.repo_args(reader.input_schema, full) or {})
        got = repos.records(payload) if payload is not None else []
        out = [*got[:1], *out]
    return out


async def _readmes(deps: TurnDeps, caller: ToolHealth, reader: McpTool | None, found: list[Item]) -> list[Item]:
    if reader is None:
        return found
    top = sorted(found, key=lambda i: -(i.stars or 0))[:TOP_READMES]
    out = list(found)
    for item in top:
        payload = await _call(deps, caller, reader, repos.repo_args(reader.input_schema, item.title) or {})
        text = repos.readme_text(payload, payload if isinstance(payload, str) else "") if payload else ""
        if text.strip():
            enriched = replace(item, text=f"{item.text}\nREADME: {' '.join(text.split())}")
            out = [enriched if i is item else i for i in out]
    return out
