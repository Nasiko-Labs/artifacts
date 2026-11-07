"""The shift-handoff LangGraph.

    START -> intake ─┬─ build (extract -> reconcile -> check -> classify -> compose) -> END
                     ├─ followup (re-render, json, mark resolved, add a note)      -> END
                     ├─ scope (one scope-check call)                               -> END
                     └─ fixed reply / pause                                        -> END

State is checkpointed per A2A contextId (thread_id): the note entries and the checked handoff, so
follow-ups work from the stored result. The graph is compiled per turn because its nodes close over that
turn's input and dependencies; the checkpointer is shared. A pause's state lives on the A2A task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import pauses
import prompts
import turns
from inputs import Incoming
from llm import TurnDeps


class HandoffState(TypedDict, total=False):
    handoff: dict | None     # handoff.Handoff.to_dict(): what follow-ups draw from
    route: str               # this turn only
    reply: str               # this turn only
    data: dict | None        # this turn only: the envelope for the data part
    pause: dict | None       # this turn only: pauses.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pauses.Pending | None = None
    data: dict | None = None


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


class ShiftHandoffAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: pauses.Pending, incoming: Incoming, context_id: str,
                     deps: TurnDeps) -> TurnResult:
        plan = intake.plan_resume(pauses.interpret(pending, incoming.text))
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", pauses.Pending.from_metadata(final.get("pause")),
                          final.get("data"))


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: HandoffState) -> dict:
        plan = scope.forced or intake.plan_turn(scope.incoming, bool(state.get("handoff")))
        scope.plan = plan
        if plan.route == "pause":
            return {"route": "", "pause": plan.pending.to_metadata()}
        if plan.route == "reply":
            return {"route": "", "reply": turns.with_notes(plan.reply, plan.extra)}
        if plan.route != "followup" and not scope.deps.traceparent:
            return {"route": "", "reply": prompts.NO_TRACE_CONTEXT}
        return {"route": plan.route}

    async def build_node(state: HandoffState) -> dict:
        return await turns.build(scope.deps, scope.plan, scope.incoming)

    async def followup_node(state: HandoffState) -> dict:
        return await turns.followup(scope.deps, scope.plan, scope.incoming, state.get("handoff"))

    async def scope_node(state: HandoffState) -> dict:
        return await turns.scope_check(scope.deps, scope.plan, scope.incoming)

    graph = StateGraph(HandoffState)
    graph.add_node("intake", intake_node)
    graph.add_node("build", build_node)
    graph.add_node("followup", followup_node)
    graph.add_node("scope", scope_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["build", "followup", "scope", END])
    for node in ("build", "followup", "scope"):
        graph.add_edge(node, END)
    return graph
