"""The alert-rule-reviewer LangGraph.

    START -> intake ─┬─ review (parse -> rules -> label -> merge -> changes -> render) -> END
                     ├─ followup (re-render, show config, scoped question)             -> END
                     └─ fixed reply / pause ───────────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the pasted files, the alerts with their verbatim
snippets and the last envelope, so follow-ups ("only the high ones", "show the config for HighCPU") work
from the stored result. The graph is compiled per turn because its nodes close over that turn's input and
dependencies; the checkpointer is shared. A pause leaves the graph as a `pause` value; its state lives on
the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import turns
from inputs import Incoming
from llm import TurnDeps


class ReviewState(TypedDict, total=False):
    files: dict               # file name -> pasted text (line numbers index this)
    alerts: list[dict]        # id, name, file, line, snippet
    envelope: dict            # the last review
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class ReviewerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to this agent's question, on the paused task."""
        route = turns.resume_route(hitl.interpret(pending, incoming.text))
        resumed = Incoming(incoming.text, incoming.policy, incoming.wants_json or pending.wants_json, incoming.notes)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: ReviewState) -> dict:
        return turns.intake(state, scope)

    async def review_node(state: ReviewState) -> dict:
        return await turns.run_review(state, scope)

    async def followup_node(state: ReviewState) -> dict:
        return await turns.run_followup(state, scope)

    graph = StateGraph(ReviewState)
    graph.add_node("intake", intake_node)
    graph.add_node("review", review_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["review", "followup", END])
    graph.add_edge("review", END)
    graph.add_edge("followup", END)
    return graph
