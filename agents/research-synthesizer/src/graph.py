"""The research-synthesizer LangGraph.

    START -> intake ─┬─ synth / add (extract -> cluster -> relate -> score -> unknowns -> compose) -> END
                     ├─ ask (one quote-checked question about the stored brief)                  -> END
                     ├─ scope (one call: decline, or pause for the sources)                      -> END
                     └─ fixed reply / re-render view / pause ─────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the stored brief (sources, claims, findings, unknowns), so
follow-ups ("only the conflicts", "what do the sources say about X?", "add this source") work from it. The
graph is compiled per turn because its nodes close over that turn's input and dependencies; the checkpointer
is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import pauses
import turns
from inputs import Incoming
from llm import TurnDeps


class ResearchState(TypedDict, total=False):
    brief: dict | None       # the stored brief
    route: str               # this turn only
    reply: str               # this turn only
    data: dict | None        # this turn only: the envelope to attach as a data part
    pause: dict | None       # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class ResearchAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        plan = intake.plan_resume(pauses.interpret(pending, incoming.text))
        resumed = replace(incoming, wants_json=incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed, forced=plan), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: ResearchState) -> dict:
        return turns.intake_node(state, scope)

    async def synth_node(state: ResearchState) -> dict:
        return await turns.synth_node(state, scope)

    async def ask_node(state: ResearchState) -> dict:
        return await turns.ask_node(state, scope)

    async def scope_node(state: ResearchState) -> dict:
        return await turns.scope_node(state, scope)

    graph = StateGraph(ResearchState)
    graph.add_node("intake", intake_node)
    graph.add_node("synth", synth_node)
    graph.add_node("ask", ask_node)
    graph.add_node("scope", scope_node)
    graph.add_edge(START, "intake")
    routes = {"synth": "synth", "add": "synth", "ask": "ask", "scope": "scope"}
    graph.add_conditional_edges("intake", lambda s: routes.get(s.get("route") or "", END),
                                ["synth", "ask", "scope", END])
    for node in ("synth", "ask", "scope"):
        graph.add_edge(node, END)
    return graph
