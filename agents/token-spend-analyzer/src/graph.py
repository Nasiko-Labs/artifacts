"""The token-spend-analyzer LangGraph.

    START → intake ─┬─ analyze (detect → parse → normalise → aggregate → waste → recommend → check → render) → END
                    ├─ followup (re-render / recompute / what-if / filter in code, or the scope check)      → END
                    └─ canned reply                                                                         → END

State is checkpointed per A2A contextId (thread_id): the stored analysis result and the normalised frame as a
compact canonical CSV, so follow-ups work from them. The graph is compiled per turn because its nodes close over
that turn's input and dependencies; the checkpointer is shared. Per-turn output stays on the turn's scope."""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup_turn
import hitl
import smalltalk
import turns
from inputs import Incoming, kind_of, split
from llm import TurnDeps
from turns import TurnResult


class SpendState(TypedDict, total=False):
    canonical: dict      # the normalised frame as canonical CSV + its metadata
    result: dict         # the last analysis result (render + envelope source)
    route: str           # this turn only


class _Scope:
    """One turn's input, dependencies and output (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, pending: hitl.Pending | None = None):
        self.deps, self.incoming, self.pending = deps, incoming, pending
        self.source, self.kind, self.instruction = "", "", ""
        self.out: TurnResult = TurnResult("")


class SpendAgent:
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
    async def intake_node(state: SpendState) -> dict:
        return _intake(scope)

    async def analyze_node(state: SpendState) -> dict:
        if scope.pending is not None:
            scope.out = await turns.resume(scope.pending, scope.incoming, scope.deps)
        else:
            scope.out = await turns.analyze(scope.incoming, scope.deps, scope.source, scope.kind, scope.instruction)
        return _stored(scope.out)

    async def followup_node(state: SpendState) -> dict:
        result = state.get("result")
        if not result:
            scope.out = await followup_turn.nothing_stored(scope.incoming, scope.deps)
            return {}
        scope.out = await followup_turn.on_stored(scope.incoming, scope.deps, result, state.get("canonical") or {})
        return _stored(scope.out)

    graph = StateGraph(SpendState)
    graph.add_node("intake", intake_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["analyze", "followup", END])
    graph.add_edge("analyze", END)
    graph.add_edge("followup", END)
    return graph


def _intake(scope: _Scope) -> dict:
    if scope.pending is not None:
        return {"route": "analyze"}
    incoming = scope.incoming
    if not incoming.file_text:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            scope.out = turns.reply(canned, incoming.notes)
            return {"route": ""}
    if incoming.file_text:
        scope.source, scope.kind = incoming.file_text, kind_of(incoming.file_text)
        scope.instruction = incoming.text.strip()[:300]
    else:
        req = split(incoming.text)
        scope.source, scope.kind, scope.instruction = req.source, req.kind, req.instruction
    return {"route": "analyze" if scope.source else "followup"}


def _stored(out: TurnResult) -> dict:
    return dict(out.stored) if out.stored else {}
