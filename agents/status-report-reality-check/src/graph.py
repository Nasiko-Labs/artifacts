"""The status-report-reality-check LangGraph.

    START -> intake ─┬─ run (extract -> ground -> match -> compare -> render) -> END
                     ├─ recompare (new thresholds / as-of / status map, zero calls) -> END
                     ├─ followup (views, matched tickets, why, json; zero calls)  -> END
                     ├─ scope (one classify call: decline / explain)              -> END
                     └─ fixed reply / pause ─────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the checked report items with their matches, the parsed
tickets, the options and the envelope, so follow-ups work from the stored comparison. The graph is compiled
per turn because its nodes close over that turn's input and dependencies; the checkpointer is shared. A pause
leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import pauses
import turns
from inputs import Incoming
from llm import TurnDeps

NODES = ("run", "recompare", "followup", "scope")


class CheckState(TypedDict, total=False):
    items: list[dict]         # checked report items with their matches
    tickets: list[dict]       # the parsed export (<= 1,500 rows)
    options: dict             # Options.to_state()
    meta: dict                # parse caveats, dropped count, coverage, fallback
    envelope: dict            # the last result
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class RealityCheckAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        route = intake.plan_resume(pauses.interpret(pending, incoming.text), pending)
        resumed = Incoming(incoming.text, incoming.policy, incoming.wants_json or pending.wants_json, incoming.notes)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: CheckState) -> dict:
        return turns.intake(state, scope)

    async def run_node(state: CheckState) -> dict:
        return await turns.run(state, scope)

    async def recompare_node(state: CheckState) -> dict:
        return await turns.recompare(state, scope)

    async def followup_node(state: CheckState) -> dict:
        return await turns.followup(state, scope)

    async def scope_node(state: CheckState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(CheckState)
    graph.add_node("intake", intake_node)
    graph.add_node("run", run_node)
    graph.add_node("recompare", recompare_node)
    graph.add_node("followup", followup_node)
    graph.add_node("scope", scope_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, [*NODES, END])
    for node in NODES:
        graph.add_edge(node, END)
    return graph
