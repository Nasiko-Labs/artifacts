"""Per-request tool discovery: `tools/list`, plus the gateway's search meta-tool asked a fixed set of
provider-neutral capability queries (concurrently, no LLM), merged by name. Each read step (issue, comments,
repo, list, search, file) then gets one tool, chosen in code by the words in its name. Nothing that reads as a
write is ever chosen; meta-tools are never offered to the model (the model is offered no tools at all).
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

import prompts
from llm import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("issue_localizer.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})
SEARCH_LIMIT = 8
STEPS = ("issue", "comments", "repo", "list", "search", "file")

# (step, query): provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[str, str], ...] = (
    ("issue", "get an issue from a code host repository"),
    ("comments", "list comments on an issue in a code host repository"),
    ("repo", "get repository details including the default branch"),
    ("list", "list the contents of a directory in a code host repository"),
    ("list", "get the file tree of a repository"),
    ("search", "search code in a code host repository for a keyword"),
    ("file", "get the contents of a file in a code host repository"),
)

WRITE_VERBS = frozenset({
    "send", "post", "reply", "forward", "create", "update", "modify", "patch", "edit", "delete", "remove", "trash",
    "archive", "move", "add", "set", "mark", "pin", "unpin", "react", "invite", "join", "leave", "rename", "upload",
    "insert", "write", "publish", "schedule", "star", "unstar", "share", "submit", "import", "cancel", "complete",
    "close", "assign", "approve", "transfer", "notify", "merge", "push", "fork", "lock", "unlock", "dispatch",
    "rerun", "label", "comment", "reopen", "dismiss", "enable", "disable", "run", "trigger", "commit",
})
_READ_VERBS = frozenset({"get", "list", "read", "fetch", "search", "find", "view", "show", "retrieve", "download"})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True)
class Catalog:
    """One tool per step that has one; `searched` is False when tool search was absent or failed."""

    chosen: Mapping[str, McpTool]
    searched: bool = False

    def tool(self, step: str) -> McpTool | None:
        return self.chosen.get(step)

    @property
    def has_code_host(self) -> bool:
        return any(step in self.chosen for step in ("issue", "list", "search", "file"))


def name_tokens(name: str) -> list[str]:
    bare = name.split("__", 1)[-1]
    return [t.casefold() for t in _NON_ALNUM.split(_CAMEL.sub(" ", bare)) if t]


def looks_like_write(tool: McpTool) -> bool:
    """A write verb in the name (unless a read verb leads it, e.g. `list_issue_comments`), or a description
    that opens with a write verb ("Creates ...")."""
    tokens = name_tokens(tool.name)
    reading = bool(tokens) and tokens[0] in _READ_VERBS or (len(tokens) > 1 and tokens[1] in _READ_VERBS)
    if not reading and any(t in WRITE_VERBS for t in tokens):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


_REPO_PROPS = frozenset({"owner", "repo", "repository", "repo_name", "repo_full_name", "full_name", "project_id",
                         "project", "repo_slug", "slug", "repository_name", "repository_full_name"})


def repo_scoped(tool: McpTool) -> bool:
    """The tool addresses a repository (a repository argument, or "repo" in its name or description), so a
    generic file or workspace reader is never mistaken for the code host."""
    props = {str(k).casefold() for k in (tool.input_schema.get("properties") or {})}
    return bool(props & _REPO_PROPS) or "repo" in tool.name.casefold() or "repositor" in tool.description.casefold()


def fits(step: str, tool: McpTool) -> bool:
    """Whether a tool's name words fit a read step (and it addresses a repository)."""
    if not repo_scoped(tool):
        return False
    t = set(name_tokens(tool.name))
    content = bool(t & {"content", "contents", "tree", "directory", "dir", "ls"})
    if step == "issue":
        return "issue" in t and not t & {"comment", "comments", "list", "search", "issues"}
    if step == "comments":
        return bool(t & {"comment", "comments"}) and bool(t & {"issue", "issues"})
    if step == "repo":
        return bool(t & {"repo", "repository"}) and "get" in t and not t & {
            "content", "contents", "file", "issue", "tree", "search", "branch", "commit", "pull", "readme"}
    if step == "list":
        return content and not t & {"search", "issue", "pull", "commit"}
    if step == "search":
        return "search" in t and "code" in t
    if step == "file":
        return (content or bool(t & {"file", "blob"})) and bool(t & _READ_VERBS) and not t & {"search", "issue"}
    return False


def choose(found: Mapping[str, tuple[McpTool, ...]], listed: Iterable[McpTool]) -> dict[str, McpTool]:
    listed_tuple = tuple(listed)
    chosen = {}
    for step in STEPS:
        pool = (*found.get(step, ()), *listed_tuple)
        pick = next((t for t in pool if fits(step, t) and not looks_like_write(t)), None)
        if pick is not None:
            chosen[step] = pick
    return chosen


async def discover(deps: TurnDeps) -> tuple[Catalog | None, str | None]:
    """This request's catalog, or the fixed reply that ends the turn."""
    if deps.gateway is None:
        return None, prompts.NO_SOURCE_REPLY
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    real = tuple(t for t in listed if t.name not in HIDDEN_META)
    found: dict[str, tuple[McpTool, ...]] = {}
    searched = False
    if any(t.name == SEARCH_META for t in listed):
        found, searched = await _search(deps)
    catalog = Catalog(MappingProxyType(choose(found, real)), searched)
    log.info("tools chosen per step: %s", {k: v.name for k, v in catalog.chosen.items()})
    return catalog, None


async def _search(deps: TurnDeps) -> tuple[dict[str, tuple[McpTool, ...]], bool]:
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found: dict[str, tuple[McpTool, ...]] = {}
    answered = 0
    for (step, query), outcome in zip(DISCOVERY_QUERIES, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        answered += 1
        found = {**found, step: (*found.get(step, ()), *outcome)}
    return found, answered > 0


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
