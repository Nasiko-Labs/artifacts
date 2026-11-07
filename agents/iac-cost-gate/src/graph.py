"""The iac-cost-gate LangGraph.

    START → intake ─┬─ analyze (detect → parse → expand → price → waste → verdict → narrate → render) → END
                    ├─ followup (re-render / recompute in code, a named line, scope check)           → END
                    └─ canned reply                                                                   → END

State is checkpointed per A2A contextId (thread_id): the parsed resources, the settings, the source (for
evidence checks) and the last result, so follow-ups work from them with zero model calls. The graph is
compiled per turn because its nodes close over that turn's input and dependencies; the checkpointer is
shared. Per-turn output (reply, pause, envelope) stays on the turn's scope and is never persisted.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import hitl
import smalltalk
import turns
from inputs import Incoming, split
from llm import TurnDeps
from turns import TurnResult


class GateState(TypedDict, total=False):
    stored: dict         # {"parsed", "opts", "source", "result"} of the last analysis
    route: str           # this turn only


class _Scope:
    """One turn's input, dependencies and output (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, pending: hitl.Pending | None = None):
        self.deps, self.incoming, self.pending = deps, incoming, pending
        self.source, self.instruction = "", ""
        self.out: TurnResult = TurnResult("")


class GateAgent:
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
    async def intake_node(state: GateState) -> dict:
        return _intake(scope)

    async def analyze_node(state: GateState) -> dict:
        if scope.pending is not None:
            scope.out = await turns.resume(scope.pending, scope.incoming, scope.deps)
        else:
            scope.out = await turns.analyze(scope.incoming, scope.deps, scope.source, scope.instruction)
        return {"stored": scope.out.stored} if scope.out.stored else {}

    async def followup_node(state: GateState) -> dict:
        stored = state.get("stored")
        if not stored:
            scope.out = await turns.nothing_stored(scope.incoming, scope.deps)
            return {}
        scope.out = await turns.on_stored(scope.incoming, scope.deps, stored)
        return {"stored": scope.out.stored} if scope.out.stored else {}

    graph = StateGraph(GateState)
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
    instruction, source = split(incoming.text)
    scope.source = "\n".join(s for s in (source, incoming.file_text) if s)
    scope.instruction = instruction if source else (incoming.text.strip()[:300] if incoming.file_text else "")
    return {"route": "analyze" if scope.source else "followup"}
