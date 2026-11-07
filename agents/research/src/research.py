"""One research turn: list GitHub tools on the gateway, run the tool loop, never guess.

The fixed answers below are returned without any LLM call (GitHub dark) or in place of the
model's text (GitHub returned nothing usable), so a missing or private org is an answer.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from nasiko_mcp import McpGatewayError, McpTool, NasikoMcpClient
from prompts import SYSTEM_PROMPT
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger(__name__)

NOT_CONNECTED = "GitHub is not connected; I will not guess."
NO_FLOW = (
    "This request carried no platform trace, so GitHub could not be called; I will not guess."
)
NO_GITHUB_DATA = (
    "GitHub returned no public data for this organization: it is private or missing on GitHub. "
    "I will not guess its repositories."
)
NO_LOOKUP = "I could not look this up on GitHub, so I will not guess."
UNVERIFIED = (
    "GitHub did not return these repositories, so I will not report them: {repos}. "
    "Ask again with the exact organization name."
)

MAX_ITERATIONS = 6  # as built
_MISS_TEXT = re.compile(
    r"not found|\b404\b|does not exist|could not resolve|no (public )?repositories"
    r"|rate limit|bad credentials|validation failed",
    re.IGNORECASE,
)
_MISS_TEXT_MAX_CHARS = 1000
_REPO_LINK = re.compile(r"github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?=[/)\]\s.,;:'\"]|$)")
_PAUSED = ("approval_pending", "authorization_required")


async def run_research(
    llm: Any, model: str, query: str, gateway: NasikoMcpClient | None, traceparent: str | None
) -> str:
    if gateway is not None and not traceparent:
        log.warning("request has no traceparent: the gateway would refuse every call")
        return NO_FLOW
    tools = await _github_tools(gateway, traceparent)
    if not tools:
        return NOT_CONNECTED
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": query}]
    result = await run_tool_loop(
        llm, model, messages, tools, gateway, traceparent, max_iterations=MAX_ITERATIONS
    )
    return _answer(result)


async def _github_tools(
    gateway: NasikoMcpClient | None, traceparent: str | None
) -> tuple[McpTool, ...]:
    """Every listed tool, unchanged; empty when the gateway is absent or unreachable."""
    if gateway is None:
        log.info("no gateway configured: GitHub is not connected")
        return ()
    try:
        listed = await gateway.list_tools(traceparent)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s: %s", type(e).__name__, e)
        return ()
    # Grants decide the toolset (this agent is granted GitHub tools only); pass all unchanged.
    log.info("gateway listed %d tools", len(listed))
    return listed


def _answer(result: LoopResult) -> str:
    if result.status in _PAUSED:
        return result.text
    if not result.tools_called or not result.text.strip():
        return NO_LOOKUP
    tool_texts = _tool_texts(result.messages)
    if not any(_has_data(text) for text in tool_texts):
        return NO_GITHUB_DATA
    unverified = _unverified_repos(result.text, tool_texts)
    if unverified:
        log.warning("answer names repositories no tool returned: %s", unverified)
        return UNVERIFIED.format(repos=", ".join(sorted(unverified)))
    return result.text


def _tool_texts(messages: Sequence[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in messages if m.get("role") == "tool"]


def _unverified_repos(answer: str, tool_texts: Sequence[str]) -> set[str]:
    """github.com/<owner>/<repo> links in the answer that appear in no tool result."""
    seen = "\n".join(tool_texts).lower()
    linked = {f"{owner}/{repo}".lower() for owner, repo in _REPO_LINK.findall(answer)}
    return {repo for repo in linked if repo not in seen}


def _has_data(text: str) -> bool:
    """False for a not-found, an error, or an empty result."""
    stripped = text.strip()
    if not stripped or stripped.startswith("Tool error:"):
        return False
    try:
        value = json.loads(stripped)
    except ValueError:
        return not _is_miss_text(stripped)
    return not _is_empty(value)


def _is_miss_text(text: str) -> bool:
    """A short not-found / error sentence; long text that merely mentions one is still data."""
    return len(text) <= _MISS_TEXT_MAX_CHARS and bool(_MISS_TEXT.search(text))


def _is_empty(value: Any) -> bool:
    if value is None or value is False or value == 0:
        return True
    if isinstance(value, str):
        return not value.strip() or _is_miss_text(value)
    if isinstance(value, list):
        return all(_is_empty(item) for item in value)
    if not isinstance(value, dict):
        return False
    return _is_error_dict(value) or _is_empty_dict(value)


def _is_error_dict(value: dict) -> bool:
    """GitHub REST / GraphQL / Composio error bodies (404, 401, 403 rate limit, 422, 5xx)."""
    if value.get("error") or value.get("errors") or "documentation_url" in value:
        return True
    if value.get("successful") is False:
        return True
    message = str(value.get("message") or "")
    return bool(message) and _is_miss_text(message)


def _is_empty_dict(value: dict) -> bool:
    if not value or value.get("total_count") == 0:
        return True
    if "data" in value and "successful" in value:  # Composio's {successful, data, error} envelope
        return _is_empty(value["data"])
    return all(_is_empty(v) if isinstance(v, (dict, list)) or not v else False for v in value.values())
