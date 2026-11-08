"""Per-step tool discovery (copied from the kit; owned here): one `tools/list` per request, plus the gateway's search meta-tool asked each
step's capability queries (concurrently, `limit` 8, this request's traceparent), merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the first message, so
the tools a step needs may be missing from it. With search off the meta-tool is not listed and
`tools/list` alone is used. The model never sees the meta-tools: found tools are called directly by
their real names. This agent only reads: no step ever sees a write tool. No provider or tool is named here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

import prompts
from deps import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("access_review.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})  # recover is re-added to read steps only
SEARCH_LIMIT = 8

Step = Literal["org_members", "repo_admins", "profiles"]
Use = Literal["read"]
Source = Literal["code host"]

# (step, use, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Step, Use, Source, str], ...] = (
    ("org_members", "read", "code host", "list members of an organization with roles"),
    ("org_members", "read", "code host", "list outside collaborators of an organization"),
    ("org_members", "read", "code host", "list teams of an organization"),
    ("repo_admins", "read", "code host", "list collaborators of a repository with permissions"),
    ("repo_admins", "read", "code host", "list repositories of an organization"),
    ("repo_admins", "read", "code host", "list teams with access to a repository"),
    ("profiles", "read", "code host", "get a user profile with public email and name"),
)
STEP_SOURCE: Mapping[str, Source] = MappingProxyType({"org_members": "code host", "repo_admins": "code host",
                                                      "profiles": "code host"})
WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove", "trash",
    "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "kick", "join", "leave", "rename",
    "upload", "insert", "write", "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel",
    "complete", "close", "assign", "approve", "transfer", "notify", "merge", "rerun", "rerequest", "dispatch",
    "trigger", "revert", "push", "lock", "unlock", "dismiss", "enable", "disable", "label", "comment", "put",
    "commit", "save", "keep", "copy", "block", "unblock", "suspend", "revoke", "grant", "convert", "replace",
})
READ_VERBS = frozenset({"get", "list", "search", "fetch", "find", "read", "download", "retrieve", "describe",
                        "check"})
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def name_tokens(tool_name: str) -> tuple[str, ...]:
    return tuple(t.lower() for t in _NON_ALNUM.split(_CAMEL_BOUNDARY.sub("_", tool_name)) if t)


def is_write_tool(tool_name: str) -> bool:
    """The name reads as a write. A read about a write-sounding noun (`list_invitations`) is a read."""
    tokens = name_tokens(tool_name)
    verbs = [t for t in tokens if t in WRITE_VERBS or t in READ_VERBS]
    if verbs and verbs[0] in READ_VERBS:
        return False
    return any(token in WRITE_VERBS for token in tokens)


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


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Creates ...")."""
    if is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS) or first.startswith("re-run")


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
