"""Per-step tool discovery (copied from the ci-failure-detective; owned here): one `tools/list` per request, plus the gateway's search meta-tool asked each
step's capability queries (concurrently, `limit` 8, this request's traceparent), merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the first message, so
the tools a step needs may be missing from it. With search off the meta-tool is not listed and
`tools/list` alone is used. The model never sees the meta-tools: found tools are called directly by
their real names. Read steps never see a write tool. No provider or tool is named here.
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

log = logging.getLogger("flaky_hunter.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})  # recover is re-added to read steps only
SEARCH_LIMIT = 8

Step = Literal["runs", "jobs", "logs", "commits", "file", "ticket", "pr"]
Use = Literal["read", "write"]
Source = Literal["ci", "code host"]

# (step, use, source, query). Provider-neutral capability phrases; tune here. The `jobs` and `logs` plan
# rows run as one step (`logs`), so a run attempt's jobs and their logs are read in one tool loop.
DISCOVERY_QUERIES: tuple[tuple[Step, Use, Source, str], ...] = (
    ("runs", "read", "ci", "list recent workflow runs of a repository"),
    ("runs", "read", "ci", "list workflow runs for a branch and workflow"),
    ("runs", "read", "ci", "get a workflow run attempt"),
    ("runs", "read", "code host", "get a repository with its default branch"),
    ("logs", "read", "ci", "list jobs of a workflow run"),
    ("logs", "read", "ci", "list jobs of a workflow run attempt"),
    ("logs", "read", "ci", "get the log of a CI job"),
    ("logs", "read", "ci", "list annotations of a check run"),
    ("commits", "read", "code host", "list commits that changed a file path"),
    ("file", "read", "code host", "get file contents at a commit"),
    ("file", "read", "code host", "search code in a repository by text"),
    ("file", "read", "code host", "get a branch with its head commit"),
    ("ticket", "read", "code host", "search issues by text"),
    ("ticket", "write", "code host", "create an issue"),
    ("ticket", "write", "code host", "create a comment on an issue"),
    ("pr", "write", "code host", "create a branch from a commit"),
    ("pr", "write", "code host", "create or update a file in a branch"),
    ("pr", "write", "code host", "create a pull request"),
)
STEP_SOURCE: Mapping[str, Source] = MappingProxyType({
    "runs": "ci", "jobs": "ci", "logs": "ci", "commits": "code host", "file": "code host", "ticket": "code host",
    "pr": "code host"})


@dataclass(frozen=True)
class Catalog:
    """One step's tools. `listed` is `tools/list` minus meta-tools; `found` is what the step's queries
    returned (by use); `searched` is False when search was absent or every query failed."""

    listed: tuple[McpTool, ...]
    found: Mapping[Use, tuple[McpTool, ...]] = field(default_factory=dict)
    searched: bool = False
    recover: McpTool | None = None

    def read_tools(self) -> tuple[McpTool, ...]:
        pool = self.found.get("read") or self.listed
        reads = tuple(t for t in _unique(pool) if not looks_like_write(t))
        return (*reads, self.recover) if reads and self.recover else reads

    def write_tools(self) -> tuple[McpTool, ...]:
        pool = _unique((*self.found.get("write", ()), *self.listed))
        return tuple(t for t in pool if send_guard.is_write_tool(t.name))


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Creates ...")."""
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS) or first.startswith("re-run")


class Discoverer:
    """One per request: `tools/list` once, then each step's searches."""

    def __init__(self, deps: TurnDeps):
        self._deps = deps
        self._listed: tuple[McpTool, ...] | None = None
        self._recover: McpTool | None = None
        self._has_search = False

    async def start(self) -> str | None:
        """Lists the tools; returns the fixed reply that ends the turn, or None."""
        deps = self._deps
        if not deps.traceparent:
            return prompts.NO_TRACE_CONTEXT
        if deps.gateway is None:
            return prompts.NO_GATEWAY
        try:
            listed = await deps.gateway.list_tools(deps.traceparent)
        except McpGatewayError as e:
            log.warning("tools/list failed: %s: %s", type(e).__name__, e)
            return prompts.GATEWAY_UNREACHABLE
        self._has_search = any(t.name == SEARCH_META for t in listed)
        self._recover = next((t for t in listed if t.name == RECOVER_META), None)
        self._listed = tuple(t for t in listed if t.name not in HIDDEN_META)
        if not self._listed and not self._has_search:
            return prompts.NO_GATEWAY
        return None

    async def catalog(self, step: Step) -> Catalog:
        if self._listed is None:
            raise RuntimeError("Discoverer.start() must run first")
        if not self._has_search:
            log.info("no tool-search meta-tool listed; step %s uses tools/list alone", step)
            return Catalog(listed=self._listed, recover=self._recover)
        queries = [(use, q) for s, use, _, q in DISCOVERY_QUERIES if s == step]
        outcomes = await asyncio.gather(*(self._ask(q) for _, q in queries), return_exceptions=True)
        found: dict[Use, tuple[McpTool, ...]] = {}
        answered = 0
        for (use, query), outcome in zip(queries, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
                continue
            answered += 1
            found = {**found, use: _unique((*found.get(use, ()), *outcome))}
        if not answered:
            log.warning("every tool search failed for step %s; using tools/list alone", step)
            return Catalog(listed=self._listed, recover=self._recover)
        log.info("step %s found: %s", step, {k: [t.name for t in v] for k, v in found.items()})
        return Catalog(listed=self._listed, found=MappingProxyType(found), searched=True, recover=self._recover)

    async def _ask(self, query: str) -> tuple[McpTool, ...]:
        deps = self._deps
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
