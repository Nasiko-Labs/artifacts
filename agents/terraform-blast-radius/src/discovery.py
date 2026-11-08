"""Per-step tool discovery: one `tools/list` per request, plus the gateway's search meta-tool asked each
step's capability queries (concurrently, `limit` 8, this request's traceparent), merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the first message, so
the tools a step needs may be missing from it. With search off the meta-tool is not listed and
`tools/list` alone is used. The model never sees the meta-tools: found tools are called directly by
their real names. This agent is read-only: no step ever sees a write tool. No provider or tool is named here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import prompts
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("terraform_blast_radius.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})  # recover is re-added to read steps only
SEARCH_LIMIT = 8

Step = Literal["search", "files", "owners"]

# (step, query). Provider-neutral capability phrases; tune here. Every step only reads.
DISCOVERY_QUERIES: tuple[tuple[Step, str], ...] = (
    ("search", "search code in a repository by text"),
    ("search", "search code across repositories of an owner by text"),
    ("files", "get file contents at a ref"),
    ("files", "list files in a directory of a repository"),
    ("owners", "list commits that changed a file path"),
    ("owners", "get file contents at a ref"),
)
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove",
    "trash", "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "rename", "upload",
    "insert", "write", "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel",
    "complete", "close", "assign", "approve", "transfer", "notify", "merge", "push", "fork", "dispatch",
    "rerun", "comment", "lock", "unlock", "dismiss", "revert", "commit", "apply", "destroy", "trigger", "save",
    "store", "put", "copy", "duplicate", "restore", "download", "export",
})
# A tool counts as a code-host tool only when its name or description speaks of code or repositories, so a
# file store or other connector's "search files" is never mistaken for a code search.
_CODE_HOST_NAME = re.compile(r"repo|code|commit", re.IGNORECASE)
_CODE_HOST_DESCRIPTION = re.compile(r"\brepositor(?:y|ies)\b|\bcommits?\b", re.IGNORECASE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """One step's tools. `listed` is `tools/list` minus meta-tools; `found` is what the step's queries
    returned; `searched` is False when search was absent or every query failed."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    searched: bool = False
    recover: McpTool | None = None

    def read_tools(self) -> tuple[McpTool, ...]:
        pool = self.found or self.listed
        reads = tuple(t for t in _unique(pool) if not looks_like_write(t) and is_code_host(t))
        return (*reads, self.recover) if reads and self.recover else reads


def is_code_host(tool: McpTool) -> bool:
    return bool(_CODE_HOST_NAME.search(tool.name) or _CODE_HOST_DESCRIPTION.search(tool.description))


def name_tokens(name: str) -> tuple[str, ...]:
    return tuple(t.casefold() for t in _NON_ALNUM.split(_CAMEL.sub(" ", name)) if t)


def looks_like_write(tool: McpTool) -> bool:
    """The name has a write verb, or the description opens with one ("Creates ...")."""
    if any(token in WRITE_VERBS for token in name_tokens(tool.name)):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


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
        queries = [q for s, q in DISCOVERY_QUERIES if s == step]
        outcomes = await asyncio.gather(*(self._ask(q) for q in queries), return_exceptions=True)
        found: tuple[McpTool, ...] = ()
        answered = 0
        for query, outcome in zip(queries, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
                continue
            answered += 1
            found = _unique((*found, *outcome))
        if not answered:
            log.warning("every tool search failed for step %s; using tools/list alone", step)
            return Catalog(listed=self._listed, recover=self._recover)
        log.info("step %s found: %s", step, [t.name for t in found])
        return Catalog(listed=self._listed, found=found, searched=True, recover=self._recover)

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
