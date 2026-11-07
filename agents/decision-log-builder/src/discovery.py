"""Per-request tool discovery (copied in from the MCP kit once; owned and adapted here): `tools/list`, plus
the gateway's search meta-tool asked a fixed set of capability queries, merged by name.

With tool search on, `tools/list` holds only a handful of tools ranked against the user's first message,
so the chat and document tools this agent needs may be missing from it. The agent asks the search
meta-tool itself, once per query, concurrently, on every request (the gateway needs this request's
traceparent, so nothing is cached). No LLM call is involved. With tool search off the meta-tool is not
listed and `tools/list` alone is used.

The model never sees the meta-tools: found tools are called by their real names, so the write guard always
sees the real call. No provider or tool is named here: capability words only.
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

log = logging.getLogger("decision_log.discovery")

SEARCH_META = "nasiko_search_tools"
CALL_META = "nasiko_call_tool"
RECOVER_META = "recover_compressed"
HIDDEN_META = frozenset({SEARCH_META, CALL_META, RECOVER_META})  # never offered to the model
SEARCH_LIMIT = 8

Step = Literal["scan", "doc"]
Source = Literal["chat", "doc"]

# (step, source, query). Provider-neutral capability phrases; tune here.
DISCOVERY_QUERIES: tuple[tuple[Step, Source, str], ...] = (
    ("scan", "chat", "get my own chat user profile"),
    ("scan", "chat", "list chat channels I am a member of"),
    ("scan", "chat", "fetch recent messages in a chat channel"),
    ("scan", "chat", "fetch replies in a chat thread"),
    ("scan", "chat", "search chat messages containing a phrase"),
    ("doc", "doc", "search documents by title"),
    ("doc", "doc", "read the text content of a document"),
    ("doc", "doc", "find a page in a knowledge base by title"),
    ("doc", "doc", "append text to the end of a document"),
    ("doc", "doc", "update the content of a document or page"),
    ("doc", "doc", "create a new document with text"),
)

# Whole words (a tool name is split into words first), so "profile" never reads as "file".
_CHAT_WORDS = frozenset({"chat", "channel", "channels", "conversation", "conversations", "thread", "threads",
                         "message", "messages", "dm", "reaction", "reactions"})
_DOC_WORDS = frozenset({"doc", "docs", "document", "documents", "file", "files", "page", "pages", "drive",
                        "folder", "folders", "wiki", "note", "notes", "knowledge"})
_APPEND_TOKENS = frozenset({"append", "insert"})
_DOC_OBJECTS = frozenset({"doc", "docs", "document", "documents", "file", "page", "note"})


@dataclass(frozen=True)
class Catalog:
    """This request's tools: `listed` from `tools/list` (meta-tools removed), `found` from the step's searches."""

    listed: tuple[McpTool, ...]
    found: tuple[McpTool, ...] = ()
    provenance: Mapping[str, Source] = field(default_factory=dict)

    @property
    def tools(self) -> tuple[McpTool, ...]:
        return _unique((*self.found, *self.listed))

    def source_of(self, tool: McpTool) -> Source | None:
        return classify(tool) or self.provenance.get(tool.name)

    def _of(self, source: Source) -> tuple[McpTool, ...]:
        return tuple(t for t in self.tools if self.source_of(t) == source)

    def chat_read_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self._of("chat") if not looks_like_write(t))

    def doc_read_tools(self) -> tuple[McpTool, ...]:
        return tuple(t for t in self._of("doc") if not looks_like_write(t))

    def append_tool(self) -> McpTool | None:
        """A tool that adds text to an existing document. Tools that replace a document's content are never
        used: the log is append-only."""
        return next((t for t in self._of("doc") if _APPEND_TOKENS & set(send_guard.name_tokens(t.name))), None)

    def create_tool(self) -> McpTool | None:
        """A tool that creates a new document (not a folder) with text."""
        for tool in self._of("doc"):
            tokens = set(send_guard.name_tokens(tool.name))
            if "create" in tokens and tokens & _DOC_OBJECTS and "folder" not in tokens:
                return tool
        return None


def looks_like_write(tool: McpTool) -> bool:
    """The name reads as a write, or the description opens with a write verb ("Appends ...")."""
    if send_guard.is_write_tool(tool.name):
        return True
    words = tool.description.strip().split(maxsplit=1)
    first = words[0].strip(".,:;").casefold() if words else ""
    stems = {first, first[:-1], first[:-2], first[:-3] + "y"} if first else set()
    return bool(stems & send_guard.WRITE_VERBS)


def classify(tool: McpTool) -> Source | None:
    """The name decides when it names one side, else the description. A tool that reads as both leans to
    "doc", so it is never offered for chat reads; one with no such words at all is None (search provenance
    decides)."""
    name = set(send_guard.name_tokens(tool.name))
    described = set(send_guard.name_tokens(tool.description))
    for words in (name, described):
        chat = bool(words & _CHAT_WORDS)
        doc = bool(words & _DOC_WORDS)
        if chat != doc:
            return "chat" if chat else "doc"
    return "doc" if (name | described) & _DOC_WORDS else None


async def discover(deps: TurnDeps, step: Step) -> tuple[Catalog | None, str | None]:
    """This request's catalog for one step, or the fixed reply that ends the turn without an LLM call."""
    if not deps.traceparent:
        return None, prompts.NO_TRACE_CONTEXT
    if deps.gateway is None:
        return None, prompts.NO_CHAT_TOOL if step == "scan" else None
    try:
        listed = await deps.gateway.list_tools(deps.traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return None, prompts.GATEWAY_UNREACHABLE
    names = {t.name for t in listed}
    catalog = Catalog(listed=tuple(t for t in listed if t.name not in HIDDEN_META))
    if SEARCH_META in names:
        catalog = await _search(deps, catalog, step)
    else:
        log.info("no tool-search meta-tool listed; using tools/list alone")
    log.info("%s tools listed: %s; found by search: %s", step, [t.name for t in catalog.listed],
             [t.name for t in catalog.found])
    return catalog, None


async def _search(deps: TurnDeps, catalog: Catalog, step: Step) -> Catalog:
    queries = [(source, query) for s, source, query in DISCOVERY_QUERIES if s == step]
    outcomes = await asyncio.gather(*(_ask(deps, q) for _, q in queries), return_exceptions=True)
    found: tuple[McpTool, ...] = ()
    provenance: dict[str, Source] = {}
    for (source, query), outcome in zip(queries, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            log.warning("tool search %r failed: %s: %s", query, type(outcome).__name__, outcome)
            continue
        found = (*found, *outcome)
        provenance = {**{t.name: source for t in outcome}, **provenance}
    return Catalog(listed=catalog.listed, found=_unique(found), provenance=MappingProxyType(provenance))


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
    """First occurrence of each name wins, in order."""
    by_name: dict[str, McpTool] = {}
    for tool in tools:
        by_name.setdefault(tool.name, tool)
    return tuple(by_name.values())
