"""The blast-radius LangGraph.

    START -> intake ─┬─ analyse (parse, census, probes: code) -> judge (LLM map) -> merge (+ one_fact, render) -> END
                     ├─ judge (follow-up: re-judge only the items the new context mentions) -> merge -> END
                     ├─ scope (one classify call: decline / ask for the diff) -> END
                     └─ fixed reply / json re-render / pause --------------------------------------------> END

State is checkpointed per A2A contextId (thread_id): the diff, its context, the fired probes, the judgements
and the last envelope, so follow-ups work on any replica. The graph is compiled per turn because its nodes
close over that turn's input and dependencies; the checkpointer is shared. A pause leaves the graph as a
`pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import intake
import turns
from inputs import Incoming
from llm import TurnDeps


class ReviewState(TypedDict, total=False):
    diff: str
    context: str
    intent: str
    fired: list[dict]
    judgements: dict
    coverage: dict
    envelope: dict
    to_judge: list[str]       # this turn only
    notes: list[str]          # this turn only
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class BlastRadiusAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to Q1 "Paste the diff", on the paused task."""
        route = intake.plan_resume(hitl.interpret(incoming.text), incoming)
        resumed = Incoming(incoming.text, incoming.policy, incoming.wants_json or pending.wants_json, incoming.notes)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None, "to_judge": [], "notes": []}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: ReviewState) -> dict:
        return turns.intake(state, scope)

    async def analyse_node(state: ReviewState) -> dict:
        return turns.analyse(state, scope)

    async def judge_node(state: ReviewState) -> dict:
        return await turns.judge(state, scope)

    async def merge_node(state: ReviewState) -> dict:
        return await turns.merge(state, scope)

    async def scope_node(state: ReviewState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(ReviewState)
    for name, node in (("intake", intake_node), ("analyse", analyse_node), ("judge", judge_node),
                       ("merge", merge_node), ("scope", scope_node)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["analyse", "judge", "scope", END])
    graph.add_edge("analyse", "judge")
    graph.add_edge("judge", "merge")
    graph.add_edge("merge", END)
    graph.add_edge("scope", END)
    return graph
