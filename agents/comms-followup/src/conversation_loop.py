"""The LLM tool loop: gather (MCP) → analyze → draft → (approve → write).

Generalised from general-assistant's `run_conversation`. Differences:
- tools are discovered through `nasiko_search_tools` and offered directly
  afterwards (tool_catalog), instead of a fixed `tools/list` snapshot;
- every MCP call passes the send guard and the web-call budget first;
- `request_approval` is a second pause kind whose answer grants a one-shot
  approval the send guard consumes;
- all per-task state lives in an immutable `LoopState` that round-trips
  through A2A Task.metadata across pauses.

I/O is behind two small ports (`ChatModel`, `Gateway`) so tests drive the
whole loop with fakes.
"""

import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

import draft_policy
import local_tools as lt
import send_guard
import tool_catalog as tc
from mcp_client import McpError
from send_guard import Approval

log = logging.getLogger("comms_followup")

# Finding tools, identity, Slack (search + DMs + group DMs), Gmail, analysis,
# and a draft is ~15 steps when it goes well; the rest is slack for retries.
MAX_TOOL_ITERATIONS = 30
MAX_TOOL_RESULT_CHARS = 16_000
MAX_WEB_CALLS_PER_TASK = 5

_PAUSING_MCP_ERRORS = frozenset({"ask_required", "auth_required"})
# What the platform resumes a paused task with when the human rejected the
# gateway's approval card (oss/hitl AUTH_OUTCOME_DENIED → hitl/mod.rs answer_text).
_DENIED_ANSWERS = frozenset({"denied", "deny", "rejected", "reject", "declined", "decline"})
_SKIPPED = json.dumps({"status": "skipped", "reason": "task paused earlier in this turn"})
_OUT_OF_STEPS = (
    "I ran out of steps before finishing. Try narrowing the request (a person and a topic), "
    "and I'll pick it up from there."
)


class ChatModel(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        """One assistant message as a plain dict (`role`, `content`, `tool_calls`)."""


class Gateway(Protocol):
    async def list_tools(self) -> list[dict]: ...

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Raises `McpError` for gateway-level failures."""


@dataclass(frozen=True)
class LoopState:
    messages: tuple[dict, ...]
    catalog: tc.Catalog = field(default_factory=lambda: tc.EMPTY_CATALOG)
    approval: Approval | None = None
    web_calls: int = 0
    # Tools the gateway blocked or the human denied in this task: never called
    # again, never approved again — the agent reports instead of retrying.
    denied_tools: tuple[str, ...] = ()

    def with_message(self, message: dict) -> "LoopState":
        return replace(self, messages=(*self.messages, message))

    def with_tool_result(self, tool_call_id: str, content: str) -> "LoopState":
        return self.with_message({"role": "tool", "tool_call_id": tool_call_id, "content": content})

    def to_metadata(self) -> dict:
        # Only the most recently found/used tools travel across a pause — the
        # platform copies this metadata onto every HITL row, and the model can
        # always search again for anything that was dropped.
        return {
            "messages": list(self.messages),
            "catalog": tc.compact(self.catalog),
            "approval": self.approval.to_dict() if self.approval else None,
            "web_calls": self.web_calls,
            "denied_tools": list(self.denied_tools),
        }

    @staticmethod
    def from_metadata(metadata: dict) -> "LoopState":
        return LoopState(
            messages=tuple(metadata.get("messages") or ()),
            catalog=tc.merge(tc.EMPTY_CATALOG, list(metadata.get("catalog") or [])),
            approval=Approval.from_dict(metadata.get("approval")),
            web_calls=int(metadata.get("web_calls") or 0),
            denied_tools=tuple(str(n) for n in metadata.get("denied_tools") or ()),
        )


@dataclass(frozen=True)
class Outcome:
    kind: str  # "complete" | "input_required" | "auth_required"
    text: str
    metadata: dict | None = None
    # The platform-facing pause keys (hitl_request_id, auth_kind, options, …),
    # also put on the status message itself: the platform builds the HITL
    # question from `status.message.metadata` and links a gateway approval row
    # to this pause by its `hitl_request_id` (oss/hitl/src/repo.rs).
    hitl: dict | None = None


@dataclass(frozen=True)
class _Pause:
    kind: str  # "input_required" | "auth_required"
    text: str
    pending: dict
    hitl: dict = field(default_factory=dict)  # top-level metadata the platform hoists


def initial_state(system_prompt: str, user_content: str) -> LoopState:
    return LoopState(messages=(
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ))


async def start(model: ChatModel, gateway: Gateway, state: LoopState, now: datetime) -> Outcome:
    listed = await _list_tools(gateway)
    return await _run(model, gateway, replace(state, catalog=tc.merge(state.catalog, listed)), now)


async def resume(
    model: ChatModel, gateway: Gateway, metadata: dict, answer: str, now: datetime
) -> Outcome:
    """Continue a paused task with the human's answer (or their authorization)."""
    state = LoopState.from_metadata(metadata)
    pending = metadata.get("pending") or {}
    call_id = str(pending.get("tool_call_id") or "")
    kind = pending.get("type")

    if kind == "ask_human":
        state = state.with_tool_result(call_id, answer.strip())
    elif kind == "approval":
        requested = Approval.from_dict(pending.get("approval"))
        if requested is None:
            state = state.with_tool_result(call_id, _error("approval request was malformed"))
        else:
            granted, content = lt.interpret_approval_answer(answer, requested)
            state = replace(state, approval=granted).with_tool_result(call_id, content)
    elif kind == "mcp_retry":
        name = str(pending.get("name") or "")
        args = dict(pending.get("arguments") or {})
        if answer.strip().lower().rstrip(".!") in _DENIED_ANSWERS:
            # The human rejected the gateway's card: don't even retry.
            log.info("tool %s denied by the user at the gateway card", name)
            state, content = _denied(state, name, args, "the user denied it on the approval card")
        else:  # approved (or unknown): retry the identical call exactly once
            state, content, pause = await _call_mcp(gateway, state, name, args)
            if pause is not None:
                return _pause_outcome(state, _with_call_id(pause, call_id))
        state = state.with_tool_result(call_id, content)
    else:
        return Outcome("complete", "This task isn't waiting on anything — send a new request.")

    return await start(model, gateway, state, now)


async def _run(model: ChatModel, gateway: Gateway, state: LoopState, now: datetime) -> Outcome:
    for _ in range(MAX_TOOL_ITERATIONS):
        tools = [*lt.LOCAL_TOOL_DEFS, *tc.openai_tool_defs(state.catalog)]
        assistant = await model.complete(list(state.messages), tools)
        state = state.with_message(assistant)
        calls = assistant.get("tool_calls") or []
        if not calls:  # tool_choice="required" makes this rare; treat bare text as the answer
            return Outcome("complete", assistant.get("content") or _OUT_OF_STEPS)

        state, pause, finished = await _run_batch(gateway, state, calls, now)
        if pause is not None:  # a pause beats a finish in the same batch
            return _pause_outcome(state, pause)
        if finished is not None:
            return Outcome("complete", finished)
    log.warning("ran out of steps after %d iterations", MAX_TOOL_ITERATIONS)
    return Outcome("complete", _OUT_OF_STEPS)


async def _run_batch(
    gateway: Gateway, state: LoopState, calls: list[dict], now: datetime
) -> tuple[LoopState, _Pause | None, str | None]:
    pause: _Pause | None = None
    finished: str | None = None
    for call in calls:
        if pause is not None:
            # Every tool_call needs a response before the next LLM call.
            state = state.with_tool_result(call["id"], _SKIPPED)
            continue
        state, pause, call_finished = await _handle_call(gateway, state, call, now)
        finished = call_finished if call_finished is not None else finished
    return state, pause, finished


async def _handle_call(
    gateway: Gateway, state: LoopState, call: dict, now: datetime
) -> tuple[LoopState, _Pause | None, str | None]:
    call_id = call["id"]
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"].get("arguments") or "{}")
    except ValueError:
        args = None
    if not isinstance(args, dict):
        return state.with_tool_result(call_id, _error("arguments must be a JSON object")), None, None

    if name == lt.ASK_HUMAN:
        log.info("ask_human (options=%s)", bool(args.get("options")))
        return state, _ask_human_pause(call_id, args), None
    if name == lt.REQUEST_APPROVAL:
        return _request_approval(state, call_id, args)
    if name == lt.FINISH_TASK:
        message = str(args.get("message") or "Done.")
        mentions = draft_policy.pricing_mentions(message)
        if mentions:
            log.info("finish_task refused: pricing mention")
            return state.with_tool_result(call_id, _error(draft_policy.refusal(mentions))), None, None
        log.info("finish_task")
        done = state.with_tool_result(call_id, json.dumps({"status": "completed"}))
        return done, None, message
    if name == lt.ANALYZE_CONVERSATION:
        result = lt.analyze_conversation_result(args, now)
        _log_analysis(result)
        return state.with_tool_result(call_id, result), None, None
    if name not in state.catalog and not _is_denied(state, name):
        log.info("unknown tool %s", name)
        return state.with_tool_result(call_id, _error(_unknown_tool_message(name, state))), None, None

    state, content, pause = await _call_mcp(gateway, state, name, args)
    if pause is not None:
        return state, _with_call_id(pause, call_id), None
    return state.with_tool_result(call_id, content), None, None


def _request_approval(
    state: LoopState, call_id: str, args: dict
) -> tuple[LoopState, _Pause | None, str | None]:
    tool_name = str(args.get("tool_name") or "")
    problem = _approval_problem(state, tool_name, str(args.get("draft") or ""))
    if problem:
        log.info("request_approval rejected: %s", problem)
        return state.with_tool_result(call_id, _error(problem)), None, None
    description = str(state.catalog[tool_name].get("description", ""))
    question, options, approval = lt.approval_request(args, description)
    pending = {"type": "approval", "tool_call_id": call_id, "approval": approval.to_dict()}
    log.info("approval requested action=%s tool=%s", approval.action, approval.tool)
    # Keep the approved tool in the compact catalog that survives the pause.
    kept = replace(state, catalog=tc.touch(state.catalog, [tool_name]))
    return kept, _Pause("input_required", question, pending, options), None


def _approval_problem(state: LoopState, tool_name: str, draft: str) -> str | None:
    if not draft.strip():
        return "`draft` must be the exact text to write"
    mentions = draft_policy.pricing_mentions(draft)
    if mentions:
        return draft_policy.refusal(mentions)
    if _is_denied(state, tool_name):
        return _denied_message(tool_name, "it was already denied in this task")
    if tool_name not in state.catalog:
        if tc.SEARCH_TOOLS_META in state.catalog:
            return (
                f"`tool_name` {tool_name!r} is not a tool you have found — find the write tool with "
                f"{tc.SEARCH_TOOLS_META} first and pass its exact name. If the search does not "
                f"return one, the platform has {_NOT_GRANTED}"
            )
        return f"`tool_name` {tool_name!r} is not in your tool list: the platform has {_NOT_GRANTED}"
    if not send_guard.is_write_tool(tool_name):
        return f"{tool_name} does not write anything; name the tool that will send/save the draft"
    return None


def _ask_human_pause(call_id: str, args: dict) -> _Pause:
    question = str(args.get("question") or "Could you give me a bit more detail?")
    hitl: dict = {}
    if args.get("options"):  # selectable-options extension; the platform validates it
        hitl = {
            "options": args["options"],
            **{k: args[k] for k in ("header", "multi_select", "allow_custom_input") if k in args},
        }
    return _Pause("input_required", question, {"type": "ask_human", "tool_call_id": call_id}, hitl)


async def _call_mcp(
    gateway: Gateway, state: LoopState, name: str, args: dict
) -> tuple[LoopState, str, _Pause | None]:
    """Guard, budget, call, and fold the result back into state."""
    inner = tc.inner_calls(name, args)
    inner_names = [n for n, _ in inner]
    already_denied = [n for n in inner_names if _is_denied(state, n)]
    if already_denied:
        log.info("tool %s not called: denied earlier in this task", already_denied[0])
        return state, _denied_message(already_denied[0], "it was already denied in this task"), None
    decision = send_guard.check(inner_names, [a for _, a in inner], state.approval)
    if not decision.allowed:
        log.warning("tool %s refused by send guard (inner=%s)", name, inner_names)
        return state, _error(decision.reason), None

    web = sum(1 for inner_name in inner_names if tc.is_web_tool(inner_name, state.catalog))
    if web and state.web_calls + web > MAX_WEB_CALLS_PER_TASK:
        log.info("tool %s refused: web budget used up (%d)", name, state.web_calls)
        return state, _error(
            f"Web budget for this task ({MAX_WEB_CALLS_PER_TASK} calls) is used up — "
            "draft with the context you already have."
        ), None

    started = time.monotonic()
    try:
        result = await gateway.call_tool(name, args)
    except McpError as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.info("tool %s inner=%s → %s (%d ms)", name, inner_names, e.kind, elapsed_ms)
        if e.kind in _PAUSING_MCP_ERRORS:
            # Keep the approval: the identical call is retried after the human acts.
            return state, "", _mcp_pause(e, name, args, state.approval if decision.is_write else None)
        if e.kind == "denied":
            spent = _after_call(state, inner_names, decision.is_write, web)
            denied_state, content = _denied(spent, name, args, e.message)
            return denied_state, content, None
        return _after_call(state, inner_names, decision.is_write, web), _error(f"{e.kind}: {e.message}"), None

    rendered = render_result(result)
    log.info(
        "tool %s inner=%s → ok%s write=%s web=%d (%d ms, %d chars)",
        name, inner_names, " (isError)" if result.get("isError") else "",
        decision.is_write, web, int((time.monotonic() - started) * 1000), len(rendered),
    )
    next_state = _after_call(state, inner_names, decision.is_write, web)
    if name == tc.SEARCH_TOOLS_META:
        found = tc.tools_from_search_result(result)
        log.info("search %r found %s", args.get("query"), [t.get("name") for t in found])
        next_state = replace(next_state, catalog=tc.merge(next_state.catalog, found))
    return next_state, rendered, None


def _after_call(state: LoopState, used: list[str], was_write: bool, web_calls: int) -> LoopState:
    return replace(
        state,
        catalog=tc.touch(state.catalog, used),  # used tools stay in the saved, compact catalog
        approval=None if was_write else state.approval,  # one approval, one write
        web_calls=state.web_calls + web_calls,
    )


def _mcp_pause(error: McpError, name: str, args: dict, approval: Approval | None) -> _Pause:
    """The gateway does not block on `ask`: it answers TOOL_ASK at once with the
    id of the approval row it created. Pausing with that id is what links the
    row to this task, so resolving the card resumes this task (chat and MAF
    alike). AUTH_REQUIRED is the state the platform mirrors a gateway row as
    (router/hitl.rs auto_resolve_linked_direct_chat_row); it resumes us with
    "denied" or an authorized reply."""
    auth_kind = "mcp_tool_approval" if error.kind == "ask_required" else "mcp_connector"
    text = _approval_card_text(name, approval) if auth_kind == "mcp_tool_approval" else error.message
    return _Pause(
        "auth_required",
        text,
        {"type": "mcp_retry", "name": name, "arguments": args},
        {"auth_kind": auth_kind, "hitl_request_id": _hitl_request_id(error.data)},
    )


def _approval_card_text(name: str, approval: Approval | None) -> str:
    """The gateway's card shows only the tool name, so the pause message
    carries what is about to be written."""
    if approval is None:
        return f"Nasiko needs your approval before this agent runs {name}."
    lines = [f"Nasiko needs your approval to run {name}:", "", f"To: {approval.recipient or approval.target}"]
    if approval.subject:
        lines.append(f"Subject: {approval.subject}")
    return "\n".join([*lines, "", approval.draft])


_NOT_GRANTED = (
    "not granted this agent that tool, so you cannot do this (sending and posting are blocked "
    "for this agent). Do not try another tool for the same action; call finish_task and tell the "
    "user plainly that you cannot send it — offer to save it as a draft instead if a draft tool "
    "is in your list."
)


def _denied(state: LoopState, name: str, args: dict, reason: str) -> tuple[LoopState, str]:
    names = [n for n, _ in tc.inner_calls(name, args)]
    denied = (*state.denied_tools, *[n for n in names if n not in state.denied_tools])
    return replace(state, denied_tools=denied, approval=None), _denied_message(names[0] if names else name, reason)


def _is_denied(state: LoopState, tool: str) -> bool:
    return tool.lower() in {d.lower() for d in state.denied_tools}


def _denied_message(tool: str, reason: str) -> str:
    return json.dumps({
        "error": f"{tool} was denied by the Nasiko platform: {reason}",
        "status": "denied",
        "instruction": (
            "Do not retry this call, do not request approval for it again, and do not use another "
            "tool to do the same thing. Call finish_task and tell the user plainly that it was "
            "denied and nothing was saved or sent."
        ),
    })


def _hitl_request_id(data: dict) -> str | None:
    """Batch calls return `hitl_request_ids` (plural), single calls the singular."""
    ids = data.get("hitl_request_ids")
    if isinstance(ids, list) and ids:
        return ids[0]
    return data.get("hitl_request_id")


def render_result(result: object) -> str:
    """MCP text content as plain text (far fewer tokens than re-serialized
    JSON), bounded so one huge thread can't crowd out the rest of the context."""
    items = result.get("content") if isinstance(result, dict) else None
    texts = [i.get("text", "") for i in items or [] if isinstance(i, dict) and i.get("type") == "text"]
    body = "\n".join(texts) if texts else json.dumps(result)
    if isinstance(result, dict) and result.get("isError"):
        body = f"Tool error: {body}"
    if len(body) <= MAX_TOOL_RESULT_CHARS:
        return body
    return (
        body[:MAX_TOOL_RESULT_CHARS]
        + f"\n…[truncated {len(body) - MAX_TOOL_RESULT_CHARS} chars — narrow the query "
        "(fewer results, a date range, or one thread) if you need what was cut]"
    )


def _log_analysis(result: str) -> None:
    """Counts and stage only — never message text."""
    data = json.loads(result)
    if "error" in data:
        log.info("analyze_conversation rejected input: %s", data["error"])
        return
    log.info(
        "analyze_conversation stage=%s owed_by=%s followups_sent=%s messages=%s channels=%s",
        data.get("stage"), data.get("owed_by"), data.get("followups_already_sent"),
        data.get("message_count"), data.get("channels_used"),
    )


def _pause_outcome(state: LoopState, pause: _Pause) -> Outcome:
    metadata = {**state.to_metadata(), "pending": pause.pending, **pause.hitl}
    return Outcome(pause.kind, pause.text, metadata, dict(pause.hitl) or None)


def _with_call_id(pause: _Pause, call_id: str) -> _Pause:
    return replace(pause, pending={**pause.pending, "tool_call_id": call_id})


async def _list_tools(gateway: Gateway) -> list[dict]:
    try:
        return await gateway.list_tools()
    except McpError:
        return []


def _unknown_tool_message(name: str, state: LoopState) -> str:
    if not state.catalog:
        return (
            f"unknown tool {name}: the Nasiko MCP gateway is not reachable from this agent, so no "
            "Slack/Gmail/web tools are available. Tell the user in finish_task."
        )
    if send_guard.is_write_tool(name):
        if tc.SEARCH_TOOLS_META in state.catalog:
            return (
                f"unknown tool {name}: find it first with {tc.SEARCH_TOOLS_META}. If the search does "
                f"not return it, the platform has {_NOT_GRANTED}"
            )
        return f"unknown tool {name}: it is not in your tool list — the platform has {_NOT_GRANTED}"
    if tc.SEARCH_TOOLS_META in state.catalog:
        return f"unknown tool {name}: find it first with {tc.SEARCH_TOOLS_META}"
    return f"unknown tool {name}: use only the tools in your tool list"


def _error(message: str) -> str:
    return json.dumps({"error": message})
