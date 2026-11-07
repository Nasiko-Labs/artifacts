"""The variance-narrator LangGraph.

    START -> turn (intake -> parse -> compute -> digest -> narrate -> check -> render) -> END

One node: everything before and after the single narrate call is pure code, decided in `turns.py`. LangGraph
is here for its checkpointer: state is kept per A2A contextId (thread_id) - the parsed rows, the user's
driver notes, the settings (length, threshold, filter, period, sign) and drivers added later - so follow-ups
("board version", "only unfavourable", "add driver: ...") work from the stored table. A pause leaves the
graph as a `pause` value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import turns
from inputs import Incoming
from llm import TurnDeps


class VarianceState(TypedDict, total=False):
    rows: list[dict]          # the parsed table (line items and total rows), Decimals as strings
    symbol: str
    message: str              # the message text outside the table (driver notes)
    source: str               # the whole input, for link checks
    instruction: str          # the user's request sentences (no line named)
    settings: dict            # length, threshold, only, period, sign_mode, sign_stated
    added: dict               # line name -> driver added by "add driver: ..."
    coverage: dict
    reply: str                # this turn only
    data: dict | None         # this turn only: the envelope to attach as a data part
    pause: dict | None        # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict | None = None


class VarianceAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(turns.Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        """An answer to one of this agent's questions, on the paused task."""
        answer = hitl.interpret(pending, incoming.text)
        return await self._run(turns.Scope(deps, incoming, answer, pending), context_id)

    async def _run(self, scope: turns.Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")), final.get("data"))


def _build_graph(scope: turns.Scope) -> StateGraph:
    async def turn_node(state: VarianceState) -> dict:
        return await turns.handle(state, scope)

    graph = StateGraph(VarianceState)
    graph.add_node("turn", turn_node)
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    return graph
