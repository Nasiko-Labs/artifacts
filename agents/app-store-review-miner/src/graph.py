"""The app-store-review-miner LangGraph.

    START → intake ─┬─ fixed reply / envelope / json view ─────────────→ END
                    ├─ reviews pasted ─────────────────────────────────→ analyse → END
                    ├─ results exist ─→ followup ─┬─ answer / decline ──→ END
                    │                             └─ needs pull ─┐
                    ├─ review words, nothing pasted ─────────────→ pull ─┬─ fixed reply → END
                    │                                                    └─ fetched → analyse → END
                    └─ anything else ─→ scope (decline or format help) → END

A greeting never enters the graph (smalltalk.py). State (stored reviews, the extraction cache, the last
result) is checkpointed per A2A contextId; the graph is compiled per turn because its nodes close over
that turn's dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import smalltalk
import turn_steps as steps
from deps import TurnDeps


class MinerState(TypedDict, total=False):
    query: str
    json_out: bool
    reply: str
    route: str
    data: dict           # this turn's envelope (the DataPart), {} for fixed replies
    turn: dict           # this turn's intake facts: skipped, paste_cut, coverage
    reviews: list[dict]  # every review kept in this conversation
    next_id: int
    ours: str
    cache: dict          # review id → extracted complaints and praise
    result: dict         # the last envelope: what follow-ups answer from
    report: str


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict = field(default_factory=dict)


class MinerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, json_out: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "json_out": json_out, "reply": "", "route": "", "data": {}, "turn": {}}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("data") or {})


def _build(deps: TurnDeps) -> StateGraph:
    async def intake(state: MinerState) -> dict:
        return steps.intake(state, deps)

    async def scope(state: MinerState) -> dict:
        return await steps.scope(state, deps)

    async def followup(state: MinerState) -> dict:
        return await steps.followup(state, deps)

    async def pull(state: MinerState) -> dict:
        return await steps.pull(state, deps)

    async def analyse(state: MinerState) -> dict:
        return await steps.analyse_step(state, deps)

    graph = StateGraph(MinerState)
    for name, node in (("intake", intake), ("scope", scope), ("followup", followup), ("pull", pull),
                       ("analyse", analyse)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route, ["analyse", "followup", "pull", "scope", END])
    graph.add_conditional_edges("followup", _route, ["pull", END])
    graph.add_conditional_edges("pull", _route, ["analyse", END])
    graph.add_edge("scope", END)
    graph.add_edge("analyse", END)
    return graph


def _route(state: MinerState) -> str:
    return state.get("route") or END
