"""The support-digest LangGraph.

    START ─┬─ (stored digest, "as json" only) ──────────────→ resend → END
           ├─ (stored digest, same period, no filter) → followup ─┬─ answered / OFF_TOPIC → END
           │                                                       └─ NEEDS_PULL ─┐
           └─ (nothing yet, another period/filter, or pasted tickets) ──────────→ pull → END

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
from followup import followup_step
from parse_paste import looks_pasted
from pipeline import pull
from request import only_json, parse_request


class DigestState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    show_env: bool
    envelope: dict | None     # the last digest's envelope


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


class DigestAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # greeting / small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(replies.NO_TRACE_CONTEXT)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "show_env": False}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        envelope = final.get("envelope") if final.get("show_env") else None
        return TurnResult(final.get("reply") or replies.UNREADABLE_ANSWER, envelope)


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: DigestState) -> dict:
        return await followup_step(deps, state)

    async def pull_node(state: DigestState) -> dict:
        return await _pull(deps, state)

    graph = StateGraph(DigestState)
    for name, node in (("followup", followup), ("pull", pull_node), ("resend", _resend)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: _entry(deps, s), ["resend", "followup", "pull"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["pull", END])
    graph.add_edge("pull", END)
    graph.add_edge("resend", END)
    return graph


def _entry(deps: TurnDeps, state: DigestState) -> str:
    query = state.get("query") or ""
    stored = state.get("envelope") or {}
    if not stored:
        return "pull"
    if only_json(query):
        return "resend"
    request = parse_request(query, deps.today, deps.config)
    same_period = not request.explicit or request.period.to_dict() == stored.get("digest", {}).get("period")
    if looks_pasted(query) or request.filters.active or not same_period:
        return "pull"
    return "followup"


def _resend(state: DigestState) -> dict:
    return {"reply": render.for_followup(state.get("envelope") or {}), "show_env": True, "route": ""}


async def _pull(deps: TurnDeps, state: DigestState) -> dict:
    request = parse_request(state["query"], deps.today, deps.config)
    result = await pull(deps, state["query"], request)
    if result.envelope is None:  # a refusal or nothing found keeps the previous digest
        return {"reply": result.reply, "route": "", "show_env": False}
    return {"reply": result.reply, "envelope": result.envelope, "route": "", "show_env": True}
