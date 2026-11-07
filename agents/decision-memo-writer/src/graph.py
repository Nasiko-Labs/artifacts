"""The decision-memo LangGraph.

    START -> intake ─┬─ run      (analyse -> score -> write -> check -> render)    -> END
                     ├─ followup (re-render, override, add option/data, reweight)   -> END
                     ├─ scope    (one classify call: decline / ask for options)     -> END
                     └─ fixed reply / pause ────────────────────────────────────────> END

A greeting never reaches the graph (smalltalk.py). State is checkpointed per A2A contextId (thread_id):
the parsed brief, the checked analysis, the written memo, its basis and the envelope, so "shorter",
"recommend B instead" and "change the weights" work on later turns. The graph is compiled per turn
because its nodes close over that turn's input and dependencies; the checkpointer is shared. A pause
leaves the graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import intake
import smalltalk
import turns
from inputs import Incoming
from llm import TurnDeps


class MemoState(TypedDict, total=False):
    brief: dict
    analysis: dict
    memo: dict
    basis: dict
    override: str
    envelope: dict
    route: str              # this turn only
    reply: str              # this turn only
    data: dict | None       # this turn only: the envelope to attach as a data part
    pause: dict | None      # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class MemoAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:  # a greeting or small talk: no model call, state untouched
            return TurnResult(canned)
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        route = intake.plan_resume(hitl.interpret(pending, incoming.text), pending)
        resumed = Incoming(incoming.text, incoming.policy, incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed, forced=route), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pending = hitl.Pending.from_metadata(final.get("pause"))
        reply = final.get("reply") or (hitl.question(pending)[0] if pending else "")
        return TurnResult(reply, pending, final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: MemoState) -> dict:
        return turns.intake(state, scope)

    async def run_node(state: MemoState) -> dict:
        return await turns.run(state, scope)

    async def followup_node(state: MemoState) -> dict:
        return await turns.followup(state, scope)

    async def scope_node(state: MemoState) -> dict:
        return await turns.scope_check(state, scope)

    graph = StateGraph(MemoState)
    graph.add_node("intake", intake_node)
    graph.add_node("run", run_node)
    graph.add_node("followup", followup_node)
    graph.add_node("scope", scope_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["run", "followup", "scope", END])
    for node in ("run", "followup", "scope"):
        graph.add_edge(node, END)
    return graph
