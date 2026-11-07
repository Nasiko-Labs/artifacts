"""The MCP tools this agent knows about, discovered at runtime.

In search mode the gateway's `tools/list` returns only a handful of tools plus
its meta-tools; everything else — Slack, Gmail, and whichever web search/scrape
provider the user connected (Anakin, Firecrawl, …) — is found through
`nasiko_search_tools`. Every tool a search returns is added to the catalog and
offered to the LLM directly on the next turn, with its real input schema.

Nothing here names a provider or a tool slug: web tools are recognised by what
they do (their name/description), not by who provides them.
"""

import json
import re
from types import MappingProxyType
from typing import Mapping

from send_guard import name_tokens

SEARCH_TOOLS_META = "nasiko_search_tools"
CALL_TOOL_META = "nasiko_call_tool"
MULTI_EXECUTE_SUFFIX = "MULTI_EXECUTE_TOOL"

# OpenAI function names must match this; a gateway tool that doesn't is still
# reachable through `nasiko_call_tool`, just not offered as a direct function.
_OPENAI_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Search results accumulate across a task; OpenAI accepts at most 128 tools per
# call and a long menu dilutes the model's choice, so the oldest drop off.
MAX_CATALOG_TOOLS = 60
# What survives a pause (task metadata is copied onto every HITL row).
MAX_PERSISTED_TOOLS = 15
MAX_PERSISTED_DESCRIPTION_CHARS = 400
_GATEWAY_META_TOOLS = frozenset({SEARCH_TOOLS_META, CALL_TOOL_META, "recover_compressed"})

# Capability wording, not provider names.
_WEB_NAME_FRAGMENTS = ("scrap", "crawl", "websearch", "browse", "serp", "webpage", "website")
_WEB_NAME_TOKENS = frozenset({"web", "url", "urls", "internet"})
_WEB_DESCRIPTION_PHRASES = (
    "search the web", "web search", "scrape", "crawl", "web page", "webpage", "website",
    "internet search", "search engine",
)

Catalog = Mapping[str, dict]
EMPTY_CATALOG: Catalog = MappingProxyType({})


def merge(catalog: Catalog, tools: list[dict]) -> Catalog:
    """A new catalog with `tools` added (newest last), trimmed to the cap."""
    merged = dict(catalog)
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if name:
            merged.pop(name, None)
            merged[name] = tool
    kept = list(merged.items())[-MAX_CATALOG_TOOLS:]
    return MappingProxyType(dict(kept))


def touch(catalog: Catalog, names: list[str]) -> Catalog:
    """Mark tools as just used (moved to the newest end), so they survive `compact`."""
    return merge(catalog, [catalog[n] for n in names if n in catalog])


def compact(catalog: Catalog) -> list[dict]:
    """The slice of the catalog worth carrying across a pause: the gateway's
    meta-tools plus the most recently found/used tools, descriptions trimmed.
    Anything dropped can be found again with `nasiko_search_tools`."""
    meta = [t for name, t in catalog.items() if name in _GATEWAY_META_TOOLS]
    recent = [t for name, t in catalog.items() if name not in _GATEWAY_META_TOOLS]
    return [_trimmed(t) for t in [*meta, *recent[-MAX_PERSISTED_TOOLS:]]]


def _trimmed(tool: dict) -> dict:
    description = str(tool.get("description", ""))
    if len(description) <= MAX_PERSISTED_DESCRIPTION_CHARS:
        return tool
    return {**tool, "description": description[: MAX_PERSISTED_DESCRIPTION_CHARS - 1] + "…"}


def openai_tool_defs(catalog: Catalog) -> list[dict]:
    return [d for d in (_openai_def(tool) for tool in catalog.values()) if d is not None]


def _openai_def(tool: dict) -> dict | None:
    name = tool.get("name", "")
    if not _OPENAI_TOOL_NAME.match(name):
        return None
    schema = dict(tool.get("inputSchema") or {})
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": tool.get("description", ""),
            "parameters": {"type": "object", "properties": {}, **schema},
        },
    }


def tools_from_search_result(result: dict) -> list[dict]:
    """`nasiko_search_tools` returns its matches at the top level, under
    `structuredContent`, and as JSON text in `content` — read whichever exists."""
    for candidate in (result.get("tools"), (result.get("structuredContent") or {}).get("tools")):
        if isinstance(candidate, list):
            return [t for t in candidate if isinstance(t, dict)]
    for item in result.get("content") or []:
        try:
            parsed = json.loads(item.get("text", ""))
        except (AttributeError, TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("tools"), list):
            return [t for t in parsed["tools"] if isinstance(t, dict)]
    return []


def inner_calls(name: str, arguments: dict) -> list[tuple[str, dict]]:
    """The real tool(s) a call will run: `nasiko_call_tool` and a Composio
    multi-execute batch both wrap other tools, and those inner tools are what
    the send guard and the web budget must judge."""
    if name == CALL_TOOL_META:
        inner = arguments.get("arguments")
        inner_name = str(arguments.get("name", ""))
        if inner_name == CALL_TOOL_META:
            return [(inner_name, {})]  # never unwrap the same wrapper forever
        # Recurse: `nasiko_call_tool` may wrap a multi-execute batch.
        return inner_calls(inner_name, inner if isinstance(inner, dict) else {})
    if name.endswith(MULTI_EXECUTE_SUFFIX):
        batch = arguments.get("tools")
        if isinstance(batch, list):
            return [
                (str(item.get("tool_slug") or item.get("slug") or item.get("name") or ""), item)
                for item in batch
                if isinstance(item, dict)
            ]
    return [(name, arguments)]


def is_web_tool(name: str, catalog: Catalog) -> bool:
    if name in (SEARCH_TOOLS_META, CALL_TOOL_META):
        return False
    lowered = name.lower()
    if any(fragment in lowered for fragment in _WEB_NAME_FRAGMENTS):
        return True
    if _WEB_NAME_TOKENS.intersection(name_tokens(name)):
        return True
    description = str((catalog.get(name) or {}).get("description", "")).lower()
    return any(phrase in description for phrase in _WEB_DESCRIPTION_PHRASES)
