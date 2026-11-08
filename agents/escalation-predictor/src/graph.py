"""The escalation-predictor LangGraph.

    START ─┬─ (stored result, "as json" only) ─────────────────→ resend → END
           ├─ (stored result, draft for #ids) ─────────────────→ draft → END
           ├─ (stored result) → followup ─┬─ answered / OFF_TOPIC → END
           │                              ├─ DRAFT:<ids> → draft → END
           │                              └─ NEEDS_PULL ─┐
           └─ (nothing yet, or pasted tickets) ──────────→ pull ─┬─ draft asked → draft → END
                                                                 └─ END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId; the graph is
compiled per turn because its nodes close over that turn's dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import render
import replies
import smalltalk
from deps import TurnDeps
from draft import draft_many
from followup import followup_step
from parse_paste import looks_pasted
from pipeline import pull
from request import only_json, parse_request


class EscalationState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    draft_ids: list[str]
    show_env: bool
    envelope: dict | None     # the last scored list's envelope
    targets: list[dict]       # per scored ticket: what a status-reply draft needs


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


class EscalationAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # greeting / small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(replies.NO_TRACE_CONTEXT)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "draft_ids": [], "show_env": False}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        envelope = final.get("envelope") if final.get("show_env") else None
        return TurnResult(final.get("reply") or replies.UNREADABLE_ANSWER, envelope)


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: EscalationState) -> dict:
        return await followup_step(deps, state)

    async def pull_node(state: EscalationState) -> dict:
        return await _pull(deps, state)

    async def draft(state: EscalationState) -> dict:
        return await _draft(deps, state)

    graph = StateGraph(EscalationState)
    for name, node in (("followup", followup), ("pull", pull_node), ("draft", draft), ("resend", _resend)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: _entry(deps, s), ["resend", "draft", "followup", "pull"])
    graph.add_conditional_edges("followup", _route, ["pull", "draft", END])
    graph.add_conditional_edges("pull", _route, ["draft", END])
    graph.add_edge("draft", END)
    graph.add_edge("resend", END)
    return graph


def _entry(deps: TurnDeps, state: EscalationState) -> str:
    query = state.get("query") or ""
    stored = bool(state.get("envelope"))
    request = parse_request(query, deps.config)
    if stored and only_json(query):
        return "resend"
    if stored and request.draft and request.ticket_ids:
        return "draft"
    if stored and not looks_pasted(query) and not request.focus_ids:
        return "followup"
    return "pull"


def _route(state: EscalationState) -> str:
    return state.get("route") or END


def _resend(state: EscalationState) -> dict:
    return {"reply": render.for_followup(state.get("envelope") or {}), "show_env": True, "route": ""}


async def _pull(deps: TurnDeps, state: EscalationState) -> dict:
    request = parse_request(state["query"], deps.config)
    result = await pull(deps, state["query"], request)
    if result.envelope is None:  # a refusal or nothing scored keeps the previous result
        return {"reply": result.reply, "route": "", "show_env": False}
    route = "draft" if request.draft and request.ticket_ids else ""
    return {"reply": result.reply, "envelope": result.envelope, "targets": list(result.targets), "route": route,
            "show_env": True, "draft_ids": list(request.ticket_ids)}


async def _draft(deps: TurnDeps, state: EscalationState) -> dict:
    targets = state.get("targets") or []
    if not targets:
        return {"reply": replies.NO_LIST_YET, "route": "", "show_env": False}
    ids = state.get("draft_ids") or list(parse_request(state["query"], deps.config).ticket_ids)
    if not ids:
        return {"reply": replies.WHICH_TICKET, "route": "", "show_env": False}
    drafts, unknown = await draft_many(deps, targets, ids)
    parts = [state.get("reply") or ""] if state.get("route") == "draft" and state.get("reply") else []
    parts += [render.copyable(d.ticket_id, d.customer, d.body) for d in drafts]
    if unknown:
        parts.append(replies.UNKNOWN_TICKETS.format(ids=", ".join(f"#{i}" for i in unknown)))
    envelope = {**(state.get("envelope") or {}), "drafts": [d.to_dict() for d in drafts]}
    return {"reply": "\n\n".join(p for p in parts if p) or replies.NO_LIST_YET, "envelope": envelope,
            "route": "", "show_env": True}
