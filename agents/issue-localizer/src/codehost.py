"""The code-host read side for one turn: calls the step's chosen tool with arguments mapped onto its input
schema, under the tool-call budget, a per-call timeout and the turn deadline, through ToolHealth (a tool
that is unavailable for this user is dropped; an identical failed call never reaches the gateway). It keeps
the coverage counts the reply reports."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import shapes
from discovery import Catalog
from llm import TOOL_TIMEOUT_SECS, BudgetSpent, TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from tool_health import ToolHealth, failure_text

log = logging.getLogger("issue_localizer.codehost")

SEARCH_PAGE = 20
COMMENTS_PAGE = 10

_OWNER = ("owner", "repo_owner", "org", "organization", "namespace", "user", "workspace")
_REPO = ("repo", "repo_name", "repository", "repository_name")
_FULL = ("repo_full_name", "full_name", "repository_full_name", "project_id", "project", "repo_slug", "slug")
_NUMBER = ("issue_number", "number", "issue_id", "issue_iid", "iid", "issue")
_PATH = ("path", "file_path", "filepath", "file", "dir", "directory", "dir_path")
_REF = ("ref", "branch", "sha", "tree_sha", "commit", "revision")
_QUERY = ("q", "query", "search", "keyword", "term", "search_term", "search_query")
_PAGE = ("per_page", "limit", "page_size", "perpage", "max_results")


@dataclass
class Coverage:
    dirs_listed: int = 0
    searches: int = 0
    files_read: int = 0
    truncated: int = 0
    failed: dict[str, str] = field(default_factory=dict)   # step → why
    budget_hit: bool = False


def arguments(tool: McpTool, values: Mapping[str, Any]) -> dict:
    """The logical values (owner, repo, number, path, ref, query, page) placed on the tool's own property names."""
    props = {k.casefold(): k for k in (tool.input_schema.get("properties") or {})}
    full = f"{values['owner']}/{values['repo']}" if values.get("owner") else None
    out: dict[str, Any] = {}
    has_owner = any(p in props for p in _OWNER)
    has_repo = any(p in props for p in (*_REPO, *_FULL))

    def put(names: tuple[str, ...], value: Any) -> None:
        name = next((props[n] for n in names if n in props), None)
        if name is not None and value is not None and name not in out:
            out[name] = value

    put(_OWNER, values.get("owner"))
    put(_REPO, values.get("repo") if has_owner else full)
    put(_FULL, full)
    put(_NUMBER, values.get("number"))
    put(_PATH, values.get("path"))
    put(_REF, values.get("ref"))
    query = values.get("query")
    if query is not None:
        put(_QUERY, query if has_repo or has_owner or not full else f"{query} repo:{full}")
    put(_PAGE, values.get("page"))
    return out


class CodeHost:
    def __init__(self, deps: TurnDeps, catalog: Catalog):
        self._deps = deps
        self._catalog = catalog
        self._health = ToolHealth(deps.gateway)
        self.coverage = Coverage()

    def has(self, step: str) -> bool:
        tool = self._catalog.tool(step)
        return tool is not None and self._health.is_offered(tool.name)

    async def call(self, step: str, **values: Any) -> Any | None:
        """The step's parsed payload, or None (no tool, budget or deadline reached, or the call failed)."""
        tool = self._catalog.tool(step)
        if tool is None or not self._health.is_offered(tool.name):
            return None
        if not self._deps.has_time():
            self.coverage.budget_hit = True
            return None
        try:
            self._deps.budget.take_tool_call()
        except BudgetSpent:
            self.coverage.budget_hit = True
            return None
        args = arguments(tool, values)
        timeout = max(1.0, min(TOOL_TIMEOUT_SECS, self._deps.remaining()))
        try:
            result = await asyncio.wait_for(self._health.call_tool(tool.name, args, self._deps.traceparent),
                                            timeout=timeout)
        except (TimeoutError, McpGatewayError) as e:
            log.warning("%s call %s failed: %s", step, tool.name, type(e).__name__)
            self.coverage.failed.setdefault(step, "timed out" if isinstance(e, TimeoutError) else "call failed")
            return None
        if not isinstance(result, ToolOk) or failure_text(result) is not None:
            dropped = self._health.dropped.get(tool.name)
            self.coverage.failed.setdefault(step, dropped or "the tool returned an error")
            return None
        return shapes.payload(result)
