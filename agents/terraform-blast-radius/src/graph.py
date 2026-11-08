"""The Terraform blast-radius LangGraph.

    START → intake ─┬─ analyze (parse → risk → search → owners → narrate) → END
                    ├─ search (a follow-up "search <owner/repo>") → END
                    └─ END (fixed reply, decline, follow-up answered from state, or the repository question)

A message that is only a greeting or small talk never enters the graph (smalltalk.py). State is checkpointed
per A2A contextId: the last result (follow-ups need no new calls), the repository named earlier, and the plan
waiting on the one repository question. The graph is compiled per turn because its nodes close over that
turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import smalltalk
import turns
from deps import TurnDeps
from intake import Incoming


class BlastState(TypedDict, total=False):
    query: str
    files: list[str]
    wants_json: bool
    resume: str | None     # the answer to the repository question
    reply: str
    data: dict             # the envelope for the reply's data part
    pause: dict            # this turn: the question to pause on
    route: str
    job: dict              # this turn: what analyze / search runs
    pending: dict          # the plan waiting on the repository question
    analysis: dict         # the last result
    repo: str              # remembered for this conversation


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pause: dict | None = None


class BlastRadiusAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text) if not incoming.files else None
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        return await self._invoke({"query": incoming.text, "files": list(incoming.files),
                                   "wants_json": incoming.wants_json, "resume": None}, context_id, deps)

    async def resume(self, answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._invoke({"query": "", "files": [], "wants_json": False, "resume": answer or ""},
                                  context_id, deps)

    async def _invoke(self, fresh: dict, context_id: str, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        start = {**fresh, "reply": "", "data": {}, "pause": {}, "route": "", "job": {}}
        final = await graph.ainvoke(start, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("data") or None, final.get("pause") or None)


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def intake_node(state: BlastState) -> dict:
        return await turns.intake_node(deps, state)

    async def analyze_node(state: BlastState) -> dict:
        return await turns.analyze_node(deps, state)

    async def search_node(state: BlastState) -> dict:
        return await turns.search_node(deps, state)

    graph = StateGraph(BlastState)
    graph.add_node("intake", intake_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("search", search_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["analyze", "search", END])
    graph.add_edge("analyze", END)
    graph.add_edge("search", END)
    return graph
