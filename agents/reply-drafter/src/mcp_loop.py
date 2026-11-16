"""Seamless MCP tool fallback with HITL pause/resume, for the out-of-scope path.

When a request falls outside this agent's built-in job, this module tries to
satisfy it with whatever MCP tools the operator has attached to this agent in
its MCP tab (CONNECT -> MCP servers), discovered at runtime through the Nasiko
gateway. It is deliberately tool-agnostic: it names no provider, connector or
tool family, and uses whatever is attached.

The three outcomes:
  - a tool fits and runs            -> a direct answer describing what it did
  - a tool needs approval ("ask")
    or a connector needs sign-in    -> PAUSE: the task goes to A2A auth-required
    ("auth")                           carrying the gateway's hitl_request_id, so
                                       the platform shows the approve / sign-in
                                       card and RESUMES this task when the human
                                       acts (the identical call is retried once);
                                       the same contract comms-followup uses.
  - nothing fits / none attached    -> one short, generic "connect a tool" reply

Safety: allow / ask / block and the cards are enforced server-side by the
gateway (oss/mcp-gateway) — the agent cannot bypass them. `start` and `resume`
never raise: any error (gateway unreachable, bad response, model failure) falls
through to the generic message, so this path can never break the agent's core
function; in-scope requests never reach here.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from mcp_client import McpError, call_tool, list_tools

log = logging.getLogger(__name__)

HTTP_TIMEOUT_SECS = 60.0
MAX_TOOL_ITERATIONS = 6
MAX_TOOL_RESULT_CHARS = 8_000
# The task-metadata namespace the loop state round-trips under across a pause.
METADATA_KEY = "mcp_loop"
# Meta-tools the gateway may expose in "search mode": calling one discovers more
# tools rather than doing the user's task, so it does not count as real work.
_SEARCH_META = "nasiko_search_tools"
_META_TOOLS = frozenset({_SEARCH_META, "nasiko_call_tool", "recover_compressed"})
# How the platform resumes a paused task when the human rejected the card.
_DENIED_ANSWERS = frozenset(
    {"deny", "denied", "no", "cancel", "cancelled", "canceled", "reject", "rejected", "dismiss", "dismissed"}
)
# Placeholder result for the other tool calls in a batch once one of them paused:
# every tool_call still needs a response before the next model call.
_SKIPPED = json.dumps({"status": "skipped", "reason": "task paused earlier in this turn"})

_SYSTEM = (
    "You are a Nasiko agent being asked for something outside your built-in job. "
    "You have been given a set of MCP tools the operator attached to you. "
    "If one or more of these tools can carry out the user's request, call them and then give a "
    "short, direct answer describing what you did. "
    "If none of the attached tools can do it, do not call any tool and do not guess an answer — "
    "reply with a single short sentence saying you can't do it with the tools you have. "
    "Never invent a result."
)


@dataclass(frozen=True)
class Outcome:
    """What the loop returns to the agent's graph/executor.

    kind "complete"       -> `text` is the final reply.
    kind "auth_required"  -> pause: `text` is the status message, `task_metadata`
                             round-trips the loop state for resume, and `hitl`
                             ({auth_kind, hitl_request_id}) goes on the status
                             message so the platform builds + links the card.
    """

    kind: str
    text: str
    task_metadata: dict | None = None
    hitl: dict | None = None


def _gateway() -> tuple[str | None, str | None]:
    return (os.environ.get("MCP_GATEWAY_URL") or None, os.environ.get("MCP_GATEWAY_TOKEN") or None)


def _generic_message(intro: str) -> str:
    lead = (
        "I can't do that with the tools I have right now. If an MCP tool can do it, connect it "
        "under MCP servers and attach it to me, and I'll use it."
    )
    return f"{lead}\n\n{intro}" if intro else lead


def _auth_kind(error_kind: str) -> str:
    return "mcp_tool_approval" if error_kind == "ask_required" else "mcp_connector"


def _hitl_request_id(data: dict) -> str | None:
    """Batch calls return `hitl_request_ids` (plural), single calls the singular."""
    ids = data.get("hitl_request_ids") if isinstance(data, dict) else None
    if isinstance(ids, list) and ids:
        return ids[0]
    return data.get("hitl_request_id") if isinstance(data, dict) else None


def _tool_defs(catalog: dict[str, dict]) -> list[dict]:
    defs = []
    for name, tool in catalog.items():
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


def _pause_outcome(
    messages: list[dict], did_real_work: bool, name: str, args: dict, call_id: str, error: McpError, intro: str
) -> Outcome:
    """Pause the task: the gateway already created the approval/auth row and
    returned its id; pausing with that id is what links the card to this task so
    resolving it resumes us (router/hitl.rs auto_resolve_linked_direct_chat_row).
    The pending call is retried identically once, after the human acts."""
    hitl = {"auth_kind": _auth_kind(error.kind), "hitl_request_id": _hitl_request_id(error.data)}
    task_metadata = {
        METADATA_KEY: {
            "messages": messages,
            "did_real_work": did_real_work,
            "intro": intro,
            "pending": {"name": name, "arguments": args, "tool_call_id": call_id},
        },
        **hitl,
    }
    text = error.message or "This needs your approval or sign-in in Nasiko before I can run it."
    return Outcome("auth_required", text, task_metadata, hitl)


async def _run_loop(
    http: httpx.AsyncClient, deps: Any, messages: list[dict], catalog: dict[str, dict], did_real_work: bool, intro: str
) -> Outcome:
    """Drive the LLM tool loop until it answers, pauses, or runs out of steps."""
    url, token = _gateway()
    for _ in range(MAX_TOOL_ITERATIONS):
        resp = await deps.llm.chat.completions.create(
            model=deps.model, messages=messages, tools=_tool_defs(catalog), tool_choice="auto"
        )
        message = resp.choices[0].message
        messages.append(message.model_dump(exclude_none=True))
        calls = message.tool_calls or []
        if not calls:
            text = (message.content or "").strip()
            return Outcome("complete", text if (did_real_work and text) else _generic_message(intro))

        pending_pause: tuple | None = None
        for call in calls:
            if pending_pause is not None:  # a pause earlier in this batch: answer the rest, then stop
                messages.append({"role": "tool", "tool_call_id": call.id, "content": _SKIPPED})
                continue
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except ValueError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            if name not in catalog:
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": json.dumps({"error": f"unknown tool {name}"})}
                )
                continue
            try:
                result = await call_tool(http, url, token, name, args, deps.traceparent)
            except McpError as exc:
                if exc.kind in ("ask_required", "auth_required"):
                    # Defer this call's result to the resume; pause after the batch.
                    pending_pause = (name, args, call.id, exc)
                    continue
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps({"error": f"{exc.kind}: {exc.message}"}),
                    }
                )
                continue
            if name == _SEARCH_META:
                catalog.update(_named(_search_results(result)))
            elif name not in _META_TOOLS:
                did_real_work = True
            messages.append({"role": "tool", "tool_call_id": call.id, "content": _render_result(result)})

        if pending_pause is not None:
            name, args, call_id, exc = pending_pause
            return _pause_outcome(messages, did_real_work, name, args, call_id, exc, intro)
    return Outcome("complete", _generic_message(intro))


async def start(deps: Any, user_text: str, intro: str) -> Outcome:
    """Begin the out-of-scope attempt. `deps` carries `llm`, `model` and
    `traceparent`; the gateway URL + token are in the container env. Never raises."""
    url, token = _gateway()
    if not (url and token and getattr(deps, "traceparent", None)):
        return Outcome("complete", _generic_message(intro))
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECS) as http:
            try:
                tools = await list_tools(http, url, token, deps.traceparent)
            except McpError:
                return Outcome("complete", _generic_message(intro))
            if not tools:
                return Outcome("complete", _generic_message(intro))
            messages: list[dict] = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_text},
            ]
            return await _run_loop(http, deps, messages, _named(tools), False, intro)
    except Exception:  # fail-safe: this path must never break the agent
        log.exception("mcp start failed; returning generic message")
        return Outcome("complete", _generic_message(intro))


async def resume(deps: Any, metadata: dict, answer: str) -> Outcome:
    """Continue a paused task after the human approved / signed in (or rejected).
    Retries the pending call once, then drives the loop to an answer. Never raises."""
    state = (metadata or {}).get(METADATA_KEY)
    if not isinstance(state, dict):
        return Outcome("complete", "This task isn't waiting on anything — send a new request.")
    messages = [m for m in (state.get("messages") or []) if isinstance(m, dict)]
    pending = state.get("pending") or {}
    name = str(pending.get("name") or "")
    args = pending.get("arguments")
    call_id = str(pending.get("tool_call_id") or "")
    intro = str(state.get("intro") or "")
    did_real_work = bool(state.get("did_real_work"))
    if not isinstance(args, dict):
        args = {}
    if not (messages and name and call_id):
        return Outcome("complete", _generic_message(intro))

    url, token = _gateway()
    if not (url and token and getattr(deps, "traceparent", None)):
        return Outcome("complete", _generic_message(intro))

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECS) as http:
            if (answer or "").strip().casefold().rstrip(".!") in _DENIED_ANSWERS:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps({"error": f"{name} was declined by the user; do not retry it"}),
                    }
                )
            else:
                try:
                    result = await call_tool(http, url, token, name, args, deps.traceparent)
                except McpError as exc:
                    if exc.kind in ("ask_required", "auth_required"):  # still gated: pause again
                        return _pause_outcome(messages, did_real_work, name, args, call_id, exc, intro)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps({"error": f"{exc.kind}: {exc.message}"}),
                        }
                    )
                else:
                    if name not in _META_TOOLS:
                        did_real_work = True
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": _render_result(result)})

            try:
                tools = await list_tools(http, url, token, deps.traceparent)
            except McpError:
                tools = []
            return await _run_loop(http, deps, messages, _named(tools), did_real_work, intro)
    except Exception:  # fail-safe
        log.exception("mcp resume failed; returning generic message")
        return Outcome("complete", _generic_message(intro))
