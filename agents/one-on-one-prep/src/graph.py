"""The 1:1 prep LangGraph.

    START -> intake ─┬─ run (extract -> check -> assess -> questions -> rank) -> END
                     ├─ followup (re-render, add topic, drop: zero calls)       -> END
                     ├─ ask (one call: what the notes say, or decline)          -> END
                     ├─ scope (one classify call: decline / ask for the notes)  -> END
                     └─ fixed reply / pause ─────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the last paste (notes, updates, headers) and the prep
envelope, so follow-ups work from the stored result. One person per conversation. The graph is compiled per
turn because its nodes close over that turn's input and dependencies; the checkpointer is shared. A pause
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


class PrepState(TypedDict, total=False):
    paste: dict               # split.Paste.to_dict() of the last run
    envelope: dict            # the last prep
    route: str                # this turn only
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class PrepAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        graph = _build_graph(turns.Scope(deps, incoming)).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        state = (await graph.aget_state(config)).values or {}
        route = intake.plan_resume(pending, pauses.interpret(pending, incoming.text), turns.stored_notes(state))
        resumed = Incoming(incoming.text, incoming.policy, incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: PrepState) -> dict:
        return turns.intake(state, scope)

    async def run_node(state: PrepState) -> dict:
        return await turns.run(state, scope)

    async def followup_node(state: PrepState) -> dict:
        return await turns.followup(state, scope)

    async def ask_node(state: PrepState) -> dict:
        return await turns.ask(state, scope)

    async def scope_node(state: PrepState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(PrepState)
    graph.add_node("intake", intake_node)
    graph.add_node("run", run_node)
    graph.add_node("followup", followup_node)
    graph.add_node("ask", ask_node)
    graph.add_node("scope", scope_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["run", "followup", "ask", "scope", END])
    for node in ("run", "followup", "ask", "scope"):
        graph.add_edge(node, END)
    return graph
