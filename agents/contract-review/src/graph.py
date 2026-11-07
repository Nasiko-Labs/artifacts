"""The contract-review LangGraph: review → human decision → decision log (gateway-approved).

State is JSON only and checkpointed per A2A task (thread_id = task id). Per-request runtime
objects come from `config["configurable"]`: `llm`, `model`, `gateway` (the MCP client or None),
`tools` (the gateway catalog filtered to read_file/save_file), `traceparent`, `today`.
Pause nodes contain only `interrupt(payload)`, because a node re-runs from its start on resume.
Every interrupt payload is `{"kind", "text", "metadata"}`; the executor turns it into an A2A
`input-required` status.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

import decisions as dec
from decision_log import (DECISION_LOG_PATH, READ_TOOL, SAVE_TOOL, GuardedGateway, msa_path, render_log,
                          save_arguments)
from llm import LlmOutputError
from openai import OpenAIError
from nasiko_hitl import NoHitlRequestError, approval_pause_metadata
from nasiko_mcp import ApprovalPending, AuthorizationRequired, McpGatewayError, ToolDenied, ToolOk
from review import Finding, ReviewError, breaks, render_review, run_review
from tool_loop import LoopResult, PendingCall, resume_tool_loop

log = logging.getLogger("contract_review.graph")

NO_PLAYBOOK = ("The playbook is not loaded for this agent, so I can't review the contract. "
               "Publish the playbook to this agent's knowledge and ask again. I won't review from memory.")
SAVE_CALL_ID = "call_save_decision_log"


class ReviewState(TypedDict, total=False):
    request: str
    rules: list[str]
    org_policy: str
    pause_mode: str  # "single" | "per_break"
    msa: str
    findings: list[dict]
    answers: list[str]
    clarification: str
    decisions: list[dict]
    log: str
    pending: dict | None  # {"kind": "read" | "write", "hitl_request_id", "loop"?}
    reply: str


def _rt(config: RunnableConfig) -> dict:
    return config["configurable"]


def _findings(state: ReviewState) -> list[Finding]:
    return [Finding.from_dict(f) for f in state.get("findings") or []]


def _pending_from(kind: str, result: ApprovalPending | AuthorizationRequired, extra: dict | None = None) -> dict:
    return {"kind": kind, "hitl_request_id": approval_pause_metadata(result)["hitl_request_id"], **(extra or {})}


# --- nodes -------------------------------------------------------------------------------------

async def check_policy(state: ReviewState) -> ReviewState:
    return {} if state.get("rules") else {"reply": NO_PLAYBOOK}


async def read_contract(state: ReviewState, config: RunnableConfig) -> ReviewState:
    if state.get("msa"):  # attached or pasted by the user
        return {"pending": None}
    rt = _rt(config)
    path = msa_path()
    if READ_TOOL not in {t.name for t in rt["tools"]}:
        return {"reply": f"The workspace read_file tool is not granted to this agent, so I can't read {path}."}
    try:
        outcome = await GuardedGateway(rt["gateway"]).call_tool(READ_TOOL, {"path": path}, rt["traceparent"])
    except McpGatewayError as e:
        return {"reply": f"I couldn't reach the workspace to read {path} ({type(e).__name__})."}
    if isinstance(outcome, (ApprovalPending, AuthorizationRequired)):
        try:
            return {"pending": _pending_from("read", outcome)}
        except NoHitlRequestError:
            return {"reply": f"Reading {path} needs approval, but no approval request could be created."}
    if isinstance(outcome, ToolDenied):
        return {"reply": f"Reading {path} was not allowed: {outcome.message}"}
    if outcome.is_error or not outcome.text.strip():
        return {"reply": f"I couldn't read {path}: {outcome.text or 'empty file'}"}
    return {"msa": outcome.text, "pending": None}


async def await_read_approval(state: ReviewState) -> ReviewState:
    pending = state["pending"] or {}
    interrupt({"kind": "approval", "text": f"Reading {msa_path()} from the workspace needs your approval.",
               "metadata": {"hitl_request_id": pending.get("hitl_request_id")}})
    return {"pending": None}


async def review(state: ReviewState, config: RunnableConfig) -> ReviewState:
    rt = _rt(config)
    try:
        findings = await run_review(rt["llm"], rt["model"], state["rules"], state.get("request", ""), state["msa"],
                                    rt["traceparent"], org_policy=state.get("org_policy", ""))
    except (ReviewError, LlmOutputError) as e:
        log.warning("review failed validation: %s", e)
        return {"reply": "I couldn't produce a review I could verify against the playbook, so I'm not "
                         f"showing one. Please run it again or use the presenter checklist. ({e})"}
    result: ReviewState = {"findings": [f.to_dict() for f in findings], "answers": []}
    if not breaks(findings):
        return {**result, "reply": f"{render_review(findings)}\n\nNo breaks, so there is nothing to decide or record."}
    return result


async def ask_all(state: ReviewState) -> ReviewState:
    answer = interrupt(dec.single_pause(_findings(state)))
    return {"answers": [str(answer)]}


async def ask_break(state: ReviewState) -> ReviewState:
    answers = state.get("answers") or []
    answer = interrupt(dec.break_pause(_findings(state), len(answers)))
    return {"answers": [*answers, str(answer)]}


async def parse_decision(state: ReviewState, config: RunnableConfig) -> ReviewState:
    rt = _rt(config)
    findings = _findings(state)
    first = state.get("decisions")  # [] is a real (empty) first pass, so test for None
    decided = first if first is not None else await _first_pass(state, findings, rt)
    clarification = state.get("clarification")
    still = dec.unresolved(findings, decided)
    if still and clarification:
        decided = [*decided, *await _parse_safely(rt, findings, still, clarification)]
        still = dec.unresolved(findings, decided)
        if still:
            return {"decisions": decided, "reply": "I still couldn't tell how to handle rule(s) "
                    f"{', '.join(map(str, still))}, so I haven't recorded anything. Nothing was written."}
    return {"decisions": decided}


async def _first_pass(state: ReviewState, findings: list[Finding], rt: dict) -> list[dict]:
    answers = state.get("answers") or []
    found = breaks(findings)
    if state.get("pause_mode") == "per_break":
        actions = [(f, dec.action_from_label(a)) for f, a in zip(found, answers)]
        return [{"rule_index": f.rule_index, "action": action, "note": ""} for f, action in actions if action]
    answer = answers[0] if answers else ""
    if dec.is_use_all(answer):
        return dec.recommended(findings)
    return await _parse_safely(rt, findings, [f.rule_index for f in found], answer)


async def _parse_safely(rt: dict, findings: list[Finding], rule_indexes: list[int], answer: str) -> list[dict]:
    """A parse that fails (bad JSON, router error) decides nothing; the human is asked again."""
    try:
        return await dec.parse_free_text(rt["llm"], rt["model"], findings, rule_indexes, answer, rt["traceparent"])
    except (LlmOutputError, OpenAIError) as e:
        log.warning("decision parse failed: %s", type(e).__name__)
        return []


async def clarify(state: ReviewState) -> ReviewState:
    findings = _findings(state)
    answer = interrupt(dec.clarify_pause(findings, dec.unresolved(findings, state.get("decisions") or [])))
    return {"clarification": str(answer)}


async def write_log(state: ReviewState, config: RunnableConfig) -> ReviewState:
    rt = _rt(config)
    content = render_log(_findings(state), state["decisions"], rt["today"])
    if SAVE_TOOL not in {t.name for t in rt["tools"]}:
        return {"log": content, "reply": "The workspace save_file tool is not granted to this agent, so nothing "
                                         f"was written. The log I would have saved:\n\n{content}"}
    args = save_arguments(content)
    guard = GuardedGateway(rt["gateway"], content)
    try:
        outcome = await guard.call_tool(SAVE_TOOL, args, rt["traceparent"])
    except McpGatewayError as e:
        return {"log": content, "reply": f"I lost contact with the workspace while saving ({type(e).__name__}). "
                                         f"Check whether {DECISION_LOG_PATH} exists before trying again."}
    if isinstance(outcome, (ApprovalPending, AuthorizationRequired)):
        try:
            return {"log": content, "pending": _pending_from("write", outcome, {"loop": _paused_save(args, outcome)})}
        except NoHitlRequestError:
            return {"log": content, "reply": "Saving needs approval, but no approval request could be created "
                                             "(no verified flow). Nothing was written."}
    return {"log": content, "reply": _save_reply(outcome, content)}


def _paused_save(args: dict, outcome: ApprovalPending | AuthorizationRequired) -> dict:
    """The code-built save call as a paused kit LoopResult, so `resume_tool_loop` can retry it."""
    call = {"id": SAVE_CALL_ID, "type": "function", "function": {"name": SAVE_TOOL, "arguments": json.dumps(args)}}
    messages = (
        {"role": "system", "content": "You record a contract decision log. After the save, confirm in one "
                                      "sentence. Do not call any tool."},
        {"role": "user", "content": f"Save the decision log to {DECISION_LOG_PATH}."},
        {"role": "assistant", "content": "", "tool_calls": [call]},
    )
    pending = PendingCall(SAVE_CALL_ID, SAVE_TOOL, args, outcome)
    return LoopResult("approval_pending", outcome.message, messages, pending).to_metadata()


async def await_write_approval(state: ReviewState) -> ReviewState:
    pending = state["pending"] or {}
    text = (f"I'm ready to save the decision log. Nasiko's approval card shows only the tool name "
            f"(save_file), so here is exactly what will be written.\n\nPath: {DECISION_LOG_PATH}\n\n"
            f"--- content ---\n{state['log']}--- end ---\n\nApprove the card to save it, or reject it to "
            "write nothing.")
    interrupt({"kind": "approval", "text": text, "metadata": {"hitl_request_id": pending.get("hitl_request_id")}})
    return {}


async def resume_write(state: ReviewState, config: RunnableConfig) -> ReviewState:
    rt = _rt(config)
    pending = state["pending"] or {}
    guard = GuardedGateway(rt["gateway"], state["log"])
    try:
        result = await resume_tool_loop(rt["llm"], rt["model"], LoopResult.from_metadata(pending["loop"]),
                                        rt["tools"], guard, rt["traceparent"], max_iterations=1, tool_choice="none")
    except Exception as e:  # the save may have happened before the confirmation call failed
        log.warning("resume continuation failed: %s", type(e).__name__)
        if SAVE_TOOL not in guard.outcomes:
            return {"pending": None, "reply": f"I couldn't retry the save ({type(e).__name__}). Nothing was written."}
        result = None
    if result is not None and result.status in ("approval_pending", "authorization_required"):
        try:
            return {"pending": _pending_from("write", result.pending.result, {"loop": result.to_metadata()})}
        except NoHitlRequestError:
            return {"pending": None, "reply": "The save still needs approval, but no approval request could be "
                                              "created. Nothing was written."}
    outcome = guard.outcomes.get(SAVE_TOOL)
    if outcome is None:
        return {"pending": None, "reply": f"The save was not retried, so {DECISION_LOG_PATH} was not written."}
    return {"pending": None, "reply": _save_reply(outcome, state["log"])}


def _save_reply(outcome: Any, content: str) -> str:
    if isinstance(outcome, ToolOk) and not outcome.is_error:
        return f"Saved {DECISION_LOG_PATH}. Nothing was sent to anyone.\n\n{content}"
    if isinstance(outcome, ToolDenied) and outcome.by_user:
        return f"You rejected the save, so {DECISION_LOG_PATH} was not written. Nothing was sent."
    if isinstance(outcome, ToolDenied):
        return f"The save was blocked ({outcome.message}), so {DECISION_LOG_PATH} was not written."
    detail = outcome.text if isinstance(outcome, ToolOk) else getattr(outcome, "message", "")
    return f"The workspace refused the save ({detail}). Nothing was written."


# --- edges -------------------------------------------------------------------------------------

def _done_or(next_node: str):
    def route(state: ReviewState) -> str:
        return END if state.get("reply") else next_node
    return route


def _after_read(state: ReviewState) -> str:
    if state.get("reply"):
        return END
    return "await_read_approval" if state.get("pending") else "review"


def _after_review(state: ReviewState) -> str:
    if state.get("reply"):
        return END
    return "ask_break" if state.get("pause_mode") == "per_break" else "ask_all"


def _after_ask_break(state: ReviewState) -> str:
    return "ask_break" if len(state.get("answers") or []) < len(breaks(_findings(state))) else "parse_decision"


def _after_parse(state: ReviewState) -> str:
    if state.get("reply"):
        return END
    return "clarify" if dec.unresolved(_findings(state), state.get("decisions") or []) else "write_log"


def _after_write(state: ReviewState) -> str:
    return END if state.get("reply") else "await_write_approval"


def build_graph(checkpointer: Any):
    g = StateGraph(ReviewState)
    for name, node in [("check_policy", check_policy), ("read_contract", read_contract),
                       ("await_read_approval", await_read_approval), ("review", review), ("ask_all", ask_all),
                       ("ask_break", ask_break), ("parse_decision", parse_decision), ("clarify", clarify),
                       ("write_log", write_log), ("await_write_approval", await_write_approval),
                       ("resume_write", resume_write)]:
        g.add_node(name, node)
    g.add_edge(START, "check_policy")
    g.add_conditional_edges("check_policy", _done_or("read_contract"))
    g.add_conditional_edges("read_contract", _after_read)
    g.add_edge("await_read_approval", "read_contract")
    g.add_conditional_edges("review", _after_review)
    g.add_edge("ask_all", "parse_decision")
    g.add_conditional_edges("ask_break", _after_ask_break)
    g.add_conditional_edges("parse_decision", _after_parse)
    g.add_edge("clarify", "parse_decision")
    g.add_conditional_edges("write_log", _after_write)
    g.add_edge("await_write_approval", "resume_write")
    g.add_conditional_edges("resume_write", _after_write)
    return g.compile(checkpointer=checkpointer)
