"""Generic MCP tool fallback for the out-of-scope path.

When a request falls outside this agent's built-in job, this module tries to
satisfy it with whatever MCP tools the operator has attached to this agent in
its MCP tab — discovered at runtime through the Nasiko gateway — and otherwise
returns one short, generic message telling the user to connect a tool.

Deliberately tool-agnostic: it names no provider, connector or tool family. The
operator attaches a server in the agent's MCP tab (CONNECT -> MCP servers), and
the agent uses whatever that exposes, whatever it is. Mirrors the platform's
"discovered provider-neutrally at runtime" design.

Safety: allow / ask / block and the approval card are enforced server-side by
the gateway (oss/mcp-gateway). A tool set to "ask", or a connector that needs
authorization, comes back as `ask_required` / `auth_required`; this module does
NOT implement approval pause-and-resume — it returns a short "approve or connect,
then ask again" message. (Full auto-resume after approval is a later step; it
needs per-agent HITL wiring like comms-followup's.)

Fail-safe: `offer_or_decline` never raises. Any error (gateway unreachable, bad
response, model failure) falls through to the generic message, so this path can
never affect the agent's core function — in-scope requests never reach here.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from mcp_client import McpError, call_tool, list_tools

log = logging.getLogger(__name__)

HTTP_TIMEOUT_SECS = 60.0
MAX_TOOL_ITERATIONS = 6
MAX_TOOL_RESULT_CHARS = 8_000
# Meta-tools the gateway may expose in "search mode": calling one discovers more
# tools rather than doing the user's task, so a call to one does not by itself
# count as having done anything.
_SEARCH_META = "nasiko_search_tools"
_META_TOOLS = frozenset({_SEARCH_META, "nasiko_call_tool", "recover_compressed"})

_SYSTEM = (
    "You are a Nasiko agent being asked for something outside your built-in job. "
    "You have been given a set of MCP tools the operator attached to you. "
    "If one or more of these tools can carry out the user's request, call them and then give a "
    "short, direct answer describing what you did. "
    "If none of the attached tools can do it, do not call any tool and do not guess an answer — "
    "reply with a single short sentence saying you can't do it with the tools you have. "
    "Never invent a result."
)


def _generic_message(intro: str) -> str:
    """No tool could do it: tell the user generically to connect one. `intro` is
    the agent's own description of what it does, so the reply still orients them."""
    lead = (
        "I can't do that with the tools I have right now. If an MCP tool can do it, connect it "
        "under MCP servers and attach it to me, and I'll use it."
    )
    return f"{lead}\n\n{intro}" if intro else lead


def _approve_or_connect_message() -> str:
    """A tool needs approval, or a connector needs authorizing — both are a user
    action in Nasiko, not something the agent can do for them."""
    return (
        "That needs your approval or a connected tool. Approve the request (or connect/authorize "
        "the tool) in Nasiko, then ask me again."
    )


def _tool_defs(tools_by_name: dict[str, dict]) -> list[dict]:
    defs = []
    for name, tool in tools_by_name.items():
        schema = dict(tool.get("inputSchema") or {})
        defs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description", "")),
                    "parameters": {"type": "object", "properties": {}, **schema},
                },
            }
        )
    return defs


def _named(tools: list[dict]) -> dict[str, dict]:
    return {t["name"]: t for t in tools if isinstance(t, dict) and t.get("name")}


def _render_result(result: Any) -> str:
    items = result.get("content") if isinstance(result, dict) else None
    texts = [i.get("text", "") for i in items or [] if isinstance(i, dict) and i.get("type") == "text"]
    body = "\n".join(texts) if texts else json.dumps(result)
    if isinstance(result, dict) and result.get("isError"):
        body = f"Tool error: {body}"
    if len(body) <= MAX_TOOL_RESULT_CHARS:
        return body
    return body[:MAX_TOOL_RESULT_CHARS] + "\n…[truncated]"


def _search_results(result: Any) -> list[dict]:
    """Tools found by `nasiko_search_tools`, read from whichever field carries them."""
    if not isinstance(result, dict):
        return []
    for candidate in (result.get("tools"), (result.get("structuredContent") or {}).get("tools")):
        if isinstance(candidate, list):
            return [t for t in candidate if isinstance(t, dict)]
    for item in result.get("content") or []:
        try:
            parsed = json.loads(item.get("text", "")) if isinstance(item, dict) else None
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("tools"), list):
            return [t for t in parsed["tools"] if isinstance(t, dict)]
    return []


async def offer_or_decline(deps: Any, user_text: str, intro: str) -> str:
    """Try the user's out-of-scope request with the agent's attached MCP tools;
    return their result, or a short generic message. Never raises.

    `deps` carries `llm` (AsyncOpenAI), `model`, and `traceparent` (all already
    built per request by the agent). The gateway credential + URL are injected
    into the container env by the platform at deploy time.
    """
    gateway_url = os.environ.get("MCP_GATEWAY_URL") or None
    token = os.environ.get("MCP_GATEWAY_TOKEN") or None
    if not (gateway_url and token and getattr(deps, "traceparent", None)):
        # No gateway wired (or no trace context to authorize a call): nothing to try.
        return _generic_message(intro)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECS) as http:
            try:
                tools = await list_tools(http, gateway_url, token, deps.traceparent)
            except McpError:
                return _generic_message(intro)
            if not tools:
                return _generic_message(intro)

            catalog = _named(tools)
            messages: list[dict] = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_text},
            ]
            did_real_work = False

            for _ in range(MAX_TOOL_ITERATIONS):
                resp = await deps.llm.chat.completions.create(
                    model=deps.model,
                    messages=messages,
                    tools=_tool_defs(catalog),
                    tool_choice="auto",
                )
                message = resp.choices[0].message
                messages.append(message.model_dump(exclude_none=True))
                calls = message.tool_calls or []
                if not calls:
                    text = (message.content or "").strip()
                    return text if (did_real_work and text) else _generic_message(intro)

                for call in calls:
                    name = call.function.name
                    try:
                        args = json.loads(call.function.arguments or "{}")
                    except ValueError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    if name not in catalog:
                        messages.append(
                            {"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps({"error": f"unknown tool {name}"})}
                        )
                        continue
                    try:
                        result = await call_tool(http, gateway_url, token, name, args, deps.traceparent)
                    except McpError as exc:
                        if exc.kind in ("ask_required", "auth_required"):
                            return _approve_or_connect_message()
                        messages.append(
                            {"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps({"error": f"{exc.kind}: {exc.message}"})}
                        )
                        continue
                    if name == _SEARCH_META:
                        catalog.update(_named(_search_results(result)))
                    elif name not in _META_TOOLS:
                        did_real_work = True
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": _render_result(result)}
                    )
            return _generic_message(intro)
    except Exception:  # fail-safe: this path must never break the agent
        log.exception("mcp tool fallback failed; returning generic message")
        return _generic_message(intro)
