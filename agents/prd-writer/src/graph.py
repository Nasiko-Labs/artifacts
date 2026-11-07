"""The PRD writer LangGraph.

    START -> intake ─┬─ run (extract -> [ask who] -> compose -> check -> [repair] -> render,
                     │       or revise, or one scope call)                          -> END
                     ├─ command (zero-call follow-ups on the stored PRD)            -> END
                     └─ fixed reply / pause ─────────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the brief, the instruction, the user's answers, the
checked facts and the current PRD, so "make requirement 3 testable" works on the stored draft. The graph is
compiled per turn because its nodes close over that turn's input and dependencies; the checkpointer is
shared. A pause leaves the graph as a `pause` value; its state lives on the A2A task.
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


class PrdState(TypedDict, total=False):
    brief: str
    instruction: str
    answers: list[str]
    facts: list[dict]
    prd: dict | None
    envelope: dict | None
    dropped: int
    cap: int
    notes: list[str]
    route: str               # this turn only
    reply: str               # this turn only
    data: dict | None        # this turn only: the envelope to attach as a data part
    pause: dict | None       # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class PrdWriterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str,
                     deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        plan = intake.plan_resume(pauses.interpret(pending, incoming.text), pending)
        resumed = replace(incoming, wants_json=incoming.wants_json or pending.wants_json)
        return await self._run(turns.Scope(deps, resumed, forced=plan, pending=pending), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def intake_node(state: PrdState) -> dict:
        return turns.intake_node(state, scope)

    async def run_node(state: PrdState) -> dict:
        return await turns.run_node(state, scope)

    async def command_node(state: PrdState) -> dict:
        return turns.command_node(state, scope)

    graph = StateGraph(PrdState)
    graph.add_node("intake", intake_node)
    graph.add_node("run", run_node)
    graph.add_node("command", command_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["run", "command", END])
    graph.add_edge("run", END)
    graph.add_edge("command", END)
    return graph
