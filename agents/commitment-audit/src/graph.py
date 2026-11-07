"""The commitment-audit LangGraph.

    START ─┬─ (an audit exists in this context) → followup ─┬─ answer ────────────────→ END
           │                                                ├─ WRITE_ROLLUP:… → prepare_write → END
           │                                                ├─ EDIT_ROLLUP → edit → END
           │                                                └─ NEEDS_AUDIT ─┐
           └─ (none) ──────────────────────────────────────────────→ audit ←┘
       audit = commitments → tracker → chat → classify → derive → render → rollup draft (audit.py)

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId (thread_id),
so a follow-up sees the audit and the rollup. The graph is compiled per turn because its nodes close
over that turn's dependencies; the checkpointer is shared. A write leaves the graph in the turn result;
the approve → write walk runs outside it, its state on the A2A task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import audit as audit_step
import drafting
import prompts
import render
import smalltalk
import targets
from approval import WriteRequest
from deps import TurnDeps
from derive import Row
from quarter import Quarter
from reader import ask_once

__all__ = ["AuditAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("commitment_audit.graph")


class AuditState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    quarter: dict
    rows: list[dict]            # the audit last shown, numbered: what "#2" refers to
    not_audited: list[str]
    rollup: str                 # the current rollup draft
    written: dict[str, str]     # target key → written / skipped / denied / blocked / not_written
    target: str                 # this turn: "doc" or "chat:<channel name>"
    write: dict | None          # this turn: the write for the approval walk


@dataclass(frozen=True)
class TurnResult:
    reply: str
    write: WriteRequest | None = None


class AuditAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "target": "", "write": None}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        write = final.get("write")
        return TurnResult(final.get("reply") or "", WriteRequest.from_dict(write) if write else None)

    async def record_write(self, context_id: str, target_key: str, status: str) -> None:
        """Remember what happened to a write, so the same rollup is not written to the same place twice."""
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("written") or {}), target_key: status}
        await graph.aupdate_state(_config(context_id), {"written": merged, "write": None}, as_node="audit")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def followup(state: AuditState) -> dict:
        return await _followup(state, deps)

    async def audit(state: AuditState) -> dict:
        return await audit_step.run(deps, state["query"])

    async def prepare_write(state: AuditState) -> dict:
        return await _prepare_write(state, deps)

    async def edit(state: AuditState) -> dict:
        return await _edit(state, deps)

    graph = StateGraph(AuditState)
    for name, node in (("followup", followup), ("audit", audit), ("prepare_write", prepare_write), ("edit", edit)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "audit"])
    graph.add_conditional_edges("followup", _route, ["audit", "prepare_write", "edit", END])
    for name in ("audit", "prepare_write", "edit"):
        graph.add_edge(name, END)
    return graph


def _entry(state: AuditState) -> str:
    return "followup" if state.get("rows") else "audit"


def _route(state: AuditState) -> str:
    return state.get("route") or END


def _rows(state: AuditState) -> tuple[Row, ...]:
    return tuple(Row.from_dict(r) for r in state.get("rows") or ())


async def _followup(state: AuditState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    quarter = Quarter.from_dict(state["quarter"])
    listing = render.render_for_followup(_rows(state), state.get("not_audited") or (), quarter)
    # Only the fenced audit and rollup, never earlier replies.
    system = (f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_audit(listing)}\n\n"
              f"{prompts.fence_rollup(state.get('rollup') or '')}")
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_AUDIT):
        return {"route": "audit"}
    if text.startswith(prompts.WRITE_PREFIX):
        return {"route": "prepare_write", "target": text[len(prompts.WRITE_PREFIX):].strip()}
    if text.startswith(prompts.EDIT_ROLLUP):
        return {"route": "edit"}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


async def _prepare_write(state: AuditState, deps: TurnDeps) -> dict:
    rollup = state.get("rollup") or ""
    if not rollup:
        return {"reply": prompts.NOTHING_TO_WRITE, "route": ""}
    quarter = Quarter.from_dict(state["quarter"])
    prepared = await targets.prepare(deps, state.get("target") or "doc", rollup,
                                     drafting.title(quarter, deps.today), state.get("written") or {})
    if prepared.request is None:
        return {"reply": prepared.reply, "route": ""}
    return {"reply": prepared.reply, "write": prepared.request.to_dict(), "route": ""}


async def _edit(state: AuditState, deps: TurnDeps) -> dict:
    rows = _rows(state)
    quarter = Quarter.from_dict(state["quarter"])
    text = await drafting.redraft(deps, rows, state.get("not_audited") or (), quarter, state.get("rollup") or "",
                                  state["query"])
    if text is None:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"reply": f"Updated rollup:\n\n{text}\n\n{render.SAVE_HINT}", "rollup": text, "written": {}, "route": ""}
