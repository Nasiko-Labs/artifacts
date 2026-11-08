"""The optional connector fetch: when nothing was pasted and a read-only cluster tool is enabled for this
agent, code (never the model) calls the found read tools, at most MAX_TOOL_CALLS, and hands their text to the
same block splitter as a paste. Output that is not kubectl-shaped is reported, never guessed at.

Arguments are filled only for parameters this code understands (a resource type, all namespaces); a tool that
requires anything else is skipped with a coverage line."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

import discovery
from llm import TurnDeps
from nasiko_mcp import McpGatewayError, McpTool, ToolOk
from tool_health import ToolHealth, failure_text

log = logging.getLogger("cluster_health.connector")

MAX_TOOL_CALLS = 8
MAX_TOOL_CHARS = 60_000
STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pods", "pods", ("pod",)), ("events", "events", ("event",)), ("nodes", "nodes", ("node",)),
    ("rollouts", "deployments", ("deploy", "rollout")), ("rollouts", "replicasets", ("replica",)),
    ("usage", "nodes", ("top", "usage", "metric")),
)
_RESOURCE_KEYS = frozenset({"resourcetype", "resource", "kind", "type", "resourcekind"})
_ALL_NS_KEYS = frozenset({"allnamespaces"})
_GENERIC = frozenset({"get", "list", "describe", "kubectl"})


@dataclass(frozen=True)
class Fetched:
    text: str                      # tool output, fed to the splitter like a paste
    notes: tuple[str, ...]         # coverage lines
    called: int


def arguments(tool: McpTool, resource: str) -> dict | None:
    schema = tool.input_schema if isinstance(tool.input_schema, Mapping) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    required = [r for r in schema.get("required") or [] if isinstance(r, str)]
    args: dict = {}
    for key in props:
        norm = key.casefold().replace("_", "").replace("-", "")
        if norm in _RESOURCE_KEYS:
            args[key] = resource
        elif norm in _ALL_NS_KEYS:
            args[key] = True
    return args if all(r in args for r in required) else None


def pick(tools: tuple[McpTool, ...], resource: str, words: tuple[str, ...]) -> McpTool | None:
    """A tool named for this resource, else a generic get/list tool that takes a resource type."""
    for tool in tools:
        if any(w in tool.name.casefold() for w in words):
            return tool
    for tool in tools:
        names = discovery.name_words(tool.name)
        if names <= _GENERIC | {"resource", "resources"} and arguments(tool, resource):
            return tool
    return None


async def fetch(deps: TurnDeps, catalog: discovery.Catalog) -> Fetched:
    tools = catalog.read_tools()
    health = ToolHealth(deps.gateway)
    texts: list[str] = []
    notes: list[str] = []
    calls = 0
    done: set[tuple[str, str]] = set()
    for step, resource, words in STEPS:
        tool = pick(tools, resource, words)
        if tool is None or (tool.name, resource) in done or calls >= MAX_TOOL_CALLS:
            notes += [] if tool is not None else [f"no read-only cluster tool for {resource}: not read"]
            continue
        args = arguments(tool, resource)
        if args is None or not health.is_offered(tool.name):
            notes.append(f"the cluster tool for {resource} needs arguments I can't fill or is unavailable: not read")
            continue
        done.add((tool.name, resource))
        calls += 1
        text, note = await _call(deps, health, tool, args, resource)
        texts += [text] if text else []
        notes += [note] if note else []
    return Fetched("\n\n".join(texts), tuple(dict.fromkeys(notes)), calls)


async def _call(deps: TurnDeps, health: ToolHealth, tool: McpTool, args: dict, resource: str) -> tuple[str, str]:
    try:
        result = await health.call_tool(tool.name, args, deps.traceparent)
    except McpGatewayError as e:
        log.warning("cluster tool %s failed: %s", tool.name, type(e).__name__)
        return "", f"the cluster tool for {resource} failed ({type(e).__name__}): not read"
    failed = failure_text(result)
    if failed is not None or not isinstance(result, ToolOk):
        reason = health.dropped.get(tool.name) or "error"
        return "", f"the cluster tool for {resource} is unavailable ({reason}): not read"
    text = result.text[:MAX_TOOL_CHARS]
    note = f"the cluster tool output for {resource} was cut at {MAX_TOOL_CHARS:,} characters" \
        if len(result.text) > MAX_TOOL_CHARS else ""
    return text, note
