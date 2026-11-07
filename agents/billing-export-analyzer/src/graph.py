"""The billing-export-analyzer LangGraph.

    START → intake ─┬─ analyze (parse → stats → evidence → narrate → check → chart → render) → END
                    ├─ followup (re-render / recompute in code, explain, cost filter)          → END
                    └─ canned reply / paste question                                          → END

State is checkpointed per A2A contextId (thread_id): the stored analysis result and the export as a compact
canonical CSV, so follow-ups work from them. The graph is compiled per turn because its nodes close over that
turn's input and dependencies; the checkpointer is shared. Per-turn output (reply, pause, envelope, PNG) stays
on the turn's scope and is never persisted.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup_turn
import hitl
import smalltalk
import turns
from inputs import Incoming, split
from llm import TurnDeps
from turns import TurnResult


class BillingState(TypedDict, total=False):
    canonical: dict      # {"csv": the export, aggregated, canonical headers} (a dict so it is stored as a blob)
    result: dict         # the last analysis result (render + envelope source)
    route: str           # this turn only


class _Scope:
    """One turn's input, dependencies and output (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, pending: hitl.Pending | None = None):
        self.deps, self.incoming, self.pending = deps, incoming, pending
        self.csv_text, self.instruction = "", ""
        self.out: TurnResult = TurnResult("")


class BillingAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming, pending), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        await graph.ainvoke({"route": ""}, {"configurable": {"thread_id": context_id}}, durability="exit")
        return scope.out


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: BillingState) -> dict:
        return _intake(state, scope)

    async def analyze_node(state: BillingState) -> dict:
        if scope.pending is not None:
            scope.out = await turns.resume(scope.pending, scope.incoming, scope.deps)
        else:
            scope.out = await turns.analyze(scope.incoming, scope.deps, scope.csv_text, scope.instruction)
        return _stored(scope.out)

    async def followup_node(state: BillingState) -> dict:
        result = state.get("result")
        if not result:
            scope.out = await followup_turn.nothing_stored(scope.incoming, scope.deps)
            return {}
        csv_text = (state.get("canonical") or {}).get("csv") or ""
        scope.out = await followup_turn.on_stored(scope.incoming, scope.deps, result, csv_text)
        return _stored(scope.out)

    graph = StateGraph(BillingState)
    graph.add_node("intake", intake_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["analyze", "followup", END])
    graph.add_edge("analyze", END)
    graph.add_edge("followup", END)
    return graph


def _intake(state: BillingState, scope: _Scope) -> dict:
    if scope.pending is not None:
        return {"route": "analyze"}
    incoming = scope.incoming
    if not incoming.file_text:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            scope.out = turns.reply(canned, incoming.notes)
            return {"route": ""}
    req = split(incoming.text)
    scope.csv_text = incoming.file_text or req.csv_text
    scope.instruction = req.instruction if not incoming.file_text else incoming.text.strip()[:300]
    return {"route": "analyze" if scope.csv_text else "followup"}


def _stored(out: TurnResult) -> dict:
    return dict(out.stored) if out.stored else {}
