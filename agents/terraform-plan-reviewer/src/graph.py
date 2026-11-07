"""The Terraform plan reviewer LangGraph.

    START -> intake ─┬─ review   (parse, rules, verdict in code; intent check + narrate) -> END
                     ├─ rerender (a view of the stored review, no call)                -> END
                     ├─ confirm  (confirm-intent follow-up, no call)                   -> END
                     ├─ ask      (one scoped call over the stored review)              -> END
                     └─ fixed reply ────────────────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the last review, so follow-ups ("only the high-risk
ones", "show evidence", "I confirm destroying X") work from it. The graph is compiled per turn because its
nodes close over that turn's input and dependencies; the checkpointer is shared. No turn pauses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import turns
from inputs import Incoming
from llm import TurnDeps


class ReviewState(TypedDict, total=False):
    review: dict          # the last review (review.run's state)
    route: str            # this turn only
    reply: str            # this turn only
    data: dict | None     # this turn only: the envelope to attach as a data part


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class PlanReviewerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(turns.Scope(deps, incoming)).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: ReviewState) -> dict:
        return turns.intake(dict(state), scope)

    async def review_node(state: ReviewState) -> dict:
        return await turns.run_review(dict(state), scope)

    async def rerender_node(state: ReviewState) -> dict:
        return turns.rerender(dict(state), scope)

    async def confirm_node(state: ReviewState) -> dict:
        return turns.confirm(dict(state), scope)

    async def ask_node(state: ReviewState) -> dict:
        return await turns.ask(dict(state), scope)

    graph = StateGraph(ReviewState)
    nodes = {"review": review_node, "rerender": rerender_node, "confirm": confirm_node, "ask": ask_node}
    graph.add_node("intake", intake_node)
    for name, node in nodes.items():
        graph.add_node(name, node)
        graph.add_edge(name, END)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, [*nodes, END])
    return graph
