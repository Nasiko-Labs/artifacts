"""The cluster-health LangGraph.

    START → turn (greeting | paste → split → parse → detect → rank → explain → render
                  | follow-up on the stored report | connector read | scope check) → END

State is checkpointed per A2A contextId (thread_id): the pasted blocks, the cluster label, the last report
and its explanations, so a follow-up ("only payments", "show rollouts") is answered in code and a later paste
(a `describe pod`) is merged with the earlier ones. The graph is compiled per turn because its node closes
over that turn's input and dependencies; the checkpointer is shared. The reply and envelope stay on the
turn's scope and are never persisted.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import turns
from inputs import Incoming
from llm import TurnDeps
from outcome import TurnResult


class HealthState(TypedDict, total=False):
    stored: dict


class HealthAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        out: list[TurnResult] = []

        async def turn_node(state: HealthState) -> dict:
            result = await turns.turn(incoming, state.get("stored"), deps)
            out.append(result)
            return {"stored": result.stored} if result.stored else {}

        graph = StateGraph(HealthState)
        graph.add_node("turn", turn_node)
        graph.add_edge(START, "turn")
        graph.add_edge("turn", END)
        compiled = graph.compile(checkpointer=self._checkpointer)
        await compiled.ainvoke({}, {"configurable": {"thread_id": context_id}}, durability="exit")
        return out[-1]
