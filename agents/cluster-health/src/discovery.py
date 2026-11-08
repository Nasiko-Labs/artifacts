"""Per-request tool discovery for the optional read-only cluster connector: `tools/list`, plus the
gateway's search meta-tool asked a fixed set of capability queries (no LLM call, concurrently), merged by
name. The model never sees any tool: code calls the found read tools directly by their real names.

Only read tools are ever callable: the name must carry a read verb (`list`, `get`, `describe`, `top`,
`logs`, `events`) and no write verb, and the description must not open with a write verb. No provider or
tool is named here."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from llm import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk

log = logging.getLogger("cluster_health.discovery")

SEARCH_META = "nasiko_search_tools"
HIDDEN_META = frozenset({SEARCH_META, "nasiko_call_tool", "recover_compressed"})
SEARCH_LIMIT = 8

DISCOVERY_QUERIES: tuple[tuple[str, str], ...] = (
    ("pods", "list pods in all namespaces with status and restarts"),
    ("pods", "get a pod with its conditions and last state"),
    ("events", "list recent events in a namespace or all namespaces"),
    ("nodes", "list nodes with conditions"),
    ("nodes", "get node resource usage"),
    ("rollouts", "list deployments with ready and desired replicas"),
    ("rollouts", "list replica sets with age"),
    ("usage", "get resource usage of pods and nodes"),
)
READ_VERBS = frozenset({"list", "get", "describe", "top", "logs", "events"})
WRITE_VERBS = frozenset({
    "create", "delete", "remove", "update", "patch", "apply", "scale", "restart", "rollback", "undo", "exec",
    "run", "cordon", "uncordon", "drain", "evict", "taint", "label", "annotate", "edit", "replace", "set",
    "install", "uninstall", "upgrade", "kill", "attach", "cp", "copy", "port", "forward", "write", "post",
    "send", "put", "deploy", "start", "stop", "terminate", "destroy", "expose", "autoscale", "rollout",
})
_WORD = re.compile(r"[a-z]+")
# Other connectors enabled for the user (a calendar's "list events", a mailbox's "get message") are not
# cluster tools: a tool is used only when its name or description speaks about the cluster.
_CLUSTER_WORD = re.compile(r"kubernetes|kubectl|\bk8s\b|cluster|\bpods?\b|namespace|\bnodes?\b|deployments?|"
                           r"replica ?sets?", re.IGNORECASE)


@dataclass(frozen=True)
class Catalog:
    tools: tuple[McpTool, ...]
    searched: bool = False

    def read_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self.tools if is_read_tool(t) and is_cluster_tool(t))


def name_words(name: str) -> set[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name.rsplit("__", 1)[-1])
    return set(_WORD.findall(spaced.casefold()))


def looks_like_write(tool: McpTool) -> bool:
    """The name carries a write verb, or the description opens with one ("Deletes ...")."""
    if name_words(tool.name) & WRITE_VERBS:
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & WRITE_VERBS)


def is_cluster_tool(tool: McpTool) -> bool:
    name = re.sub(r"[_\-]+", " ", tool.name.rsplit("__", 1)[-1])
    return bool(_CLUSTER_WORD.search(f"{name} {tool.description}"))


def is_read_tool(tool: McpTool) -> bool:
    return bool(name_words(tool.name) & READ_VERBS) and not looks_like_write(tool)


async def discover(deps: TurnDeps) -> Catalog | None:
    """This request's catalog, or None when the gateway could not be reached."""
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None
    names = {t.name for t in listed}
    real = tuple(t for t in listed if t.name not in HIDDEN_META)
    if SEARCH_META not in names:
        return Catalog(real)
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in DISCOVERY_QUERIES), return_exceptions=True)
    found = [t for o in outcomes if not isinstance(o, BaseException) for t in o]
    answered = any(not isinstance(o, BaseException) for o in outcomes)
    return Catalog(_unique((*real, *found)), searched=answered)


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
