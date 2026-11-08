"""The commitment-advisor LangGraph.

    START → intake ─┬─ analyze (parse → scopes → optimise → existing → scenarios → explain → render) → END
                    ├─ followup (re-run in code on an option change, or answer from the stored result) → END
                    └─ canned reply (greeting)                                                        → END

State is checkpointed per A2A contextId (thread_id): the last result and the paste it came from, so
follow-ups work from them on any replica. The graph is compiled per turn because its nodes close over that
turn's input and dependencies; the checkpointer is shared. Per-turn output stays on the turn's scope.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import smalltalk
import turns
from inputs import Incoming, Request, split
from llm import TurnDeps
from turns import TurnResult


class AdvisorState(TypedDict, total=False):
    paste: dict      # {"usage": csv, "commitments": csv} of the last run
    result: dict     # the last result (render + envelope source)
    route: str       # this turn only


class _Scope:
    """One turn's input, dependencies and output (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.request = Request("")
        self.out: TurnResult = TurnResult("")


class AdvisorAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        scope = _Scope(deps, incoming)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        await graph.ainvoke({"route": ""}, {"configurable": {"thread_id": context_id}}, durability="exit")
        return scope.out


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: AdvisorState) -> dict:
        return _intake(scope)

    async def analyze_node(state: AdvisorState) -> dict:
        scope.out = await turns.analyze(scope.incoming, scope.deps, scope.request)
        return _stored(scope.out)

    async def followup_node(state: AdvisorState) -> dict:
        if not state.get("result"):
            scope.out = await followup.nothing_stored(scope.incoming, scope.deps)
            return {}
        stored = {"result": state["result"], "paste": state.get("paste") or {}}
        scope.out = await followup.on_stored(scope.incoming, scope.deps, stored)
        return _stored(scope.out)

    graph = StateGraph(AdvisorState)
    graph.add_node("intake", intake_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["analyze", "followup", END])
    graph.add_edge("analyze", END)
    graph.add_edge("followup", END)
    return graph


def _intake(scope: _Scope) -> dict:
    incoming = scope.incoming
    if not incoming.file_text:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            scope.out = turns.reply(canned, incoming.notes)
            return {"route": ""}
    req = split(incoming.text)
    if incoming.file_text:
        from_file = split(incoming.file_text)
        req = Request(req.instruction, from_file.usage_csv, from_file.commitments_csv or req.commitments_csv)
    scope.request = req
    return {"route": "analyze" if req.usage_csv else "followup"}


def _stored(out: TurnResult) -> dict:
    return dict(out.stored) if out.stored else {}
