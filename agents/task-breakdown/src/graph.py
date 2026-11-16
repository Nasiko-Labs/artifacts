"""The task-breakdown LangGraph.

    START -> intake ─┬─ plan   (map chunks -> plan -> repair, in planning.py) ─┐
                     ├─ revise (revise -> reconcile ids -> repair) ────────────┼─> render -> END
                     ├─ render (an envelope loaded as the plan) ───────────────┘
                     ├─ query  (answered in code from the stored plan)        -> END
                     └─ fixed reply / pause ─────────────────────────────────> END

The checked plan is checkpointed per A2A contextId (thread_id), so revisions and questions work from it.
The graph is compiled per turn because its nodes close over that turn's input and dependencies; the
checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
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


class BreakdownState(TypedDict, total=False):
    plan: dict | None         # the checked plan (models.Plan.to_state)
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class BreakdownAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to this agent's one question, on the paused task."""
        route = intake.plan_resume(hitl.interpret(pending, incoming.text))
        resumed = Incoming(incoming.text, incoming.policy, incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: BreakdownState) -> dict:
        return await turns.intake(state, scope)

    async def plan_node(state: BreakdownState) -> dict:
        return await turns.plan(state, scope)

    async def revise_node(state: BreakdownState) -> dict:
        return await turns.revise(state, scope)

    async def render_node(state: BreakdownState) -> dict:
        return turns.render_node(state, scope)

    async def query_node(state: BreakdownState) -> dict:
        return turns.query(state, scope)

    graph = StateGraph(BreakdownState)
    for name, node in (("intake", intake_node), ("plan", plan_node), ("revise", revise_node),
                       ("render", render_node), ("query", query_node)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END,
                                ["plan", "revise", "render", "query", END])
    for node in ("plan", "revise"):
        graph.add_conditional_edges(node, lambda s: s.get("route") or END, ["render", END])
    graph.add_edge("render", END)
    graph.add_edge("query", END)
    return graph
