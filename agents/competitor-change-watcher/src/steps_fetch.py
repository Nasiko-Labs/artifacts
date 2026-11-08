"""The optional fetch: when the message names a page and nothing is pasted, a connected page-reading tool
reads it through the gateway (never from this agent's own code). The page text is exactly what the tool
returned; it joins the flow as a block with source `fetched`, named after the link's host."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import discovery
import prompts
import replies
from deps import TurnDeps
from fence import FencedGateway
from nasiko_mcp import McpGatewayError
from records import Block
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("change_watcher.fetch")
_URL = re.compile(r"https?://[^\s<>\"')\]]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,12}(?:/[^\s<>\"')\]]*)?", re.IGNORECASE)
NOTHING_READ = "The page-reading tool returned nothing for that link."


@dataclass(frozen=True)
class Fetched:
    block: Block | None = None
    reply: str | None = None


def url_in(text: str) -> str | None:
    found = _URL.search(text or "")
    return found.group(0).rstrip(".,;:!?") if found else None


def host_of(url: str) -> str:
    parsed = urlsplit(url if "://" in url else f"https://{url}")
    return (parsed.hostname or url).removeprefix("www.")


def _help(first: str) -> Fetched:
    return Fetched(reply=f"{first}\n\n{replies.FORMAT_HELP}")


async def fetch(deps: TurnDeps, url: str) -> Fetched:
    if deps.gateway is None:
        return _help(replies.URL_HINT)
    try:
        tools = (await discovery.discover(deps)).page_tools()
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", e)
        return _help(replies.GATEWAY_UNREACHABLE)
    if not tools:
        return _help(replies.URL_HINT)
    health = ToolHealth(deps.gateway)
    fenced = FencedGateway(health, deps.config.fetch_max_tool_calls)
    messages = ({"role": "system", "content": prompts.fetch_system(url)}, {"role": "user", "content": url})
    try:
        result = await run_tool_loop(deps.llm, deps.model, messages, tools, fenced, deps.traceparent,
                                     max_iterations=deps.config.fetch_max_iterations, is_offered=health.is_offered)
    except McpGatewayError as e:
        log.warning("fetch loop failed: %s", e)
        return _help(replies.GATEWAY_UNREACHABLE)
    deps.budget.calls += sum(1 for m in result.messages if m.get("role") == "assistant")
    text = fenced.seen_text.strip()
    if not text or health.dropped:
        return _help(NOTHING_READ)
    return Fetched(block=Block(competitor=host_of(url), source="fetched", body=text[:deps.config.paste_limit]))
