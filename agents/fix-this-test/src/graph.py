"""The fix-this-test LangGraph.

    START → intake ─┬─ fixed reply / pause ──────────────────────────────→ END
                    ├─ classify (1 call: off topic, ask for output, …) ─┬→ END
                    │                                                   ├→ fix
                    │                                                   └→ explain
                    ├─ fix (materialize → baseline → loop → verify → render) → END
                    └─ explain (no edits) ──────────────────────────────────→ END

A greeting never reaches the model. The parsed input (failure output, files, constraints) and the last
result are checkpointed per A2A contextId, so follow-ups ("try a different fix", "explain the fix") work
on any replica. The graph is compiled per turn because its nodes close over that turn's dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import prompts
from hitl import Answer, Pending
from inputs import Incoming
from llm import Budget, BudgetExhausted, TurnDeps
from sandbox import Sandbox
from steps import Scope, classify_step, explain_step, fix_step

log = logging.getLogger("fix-this-test.graph")


class FixState(TypedDict, total=False):
    failure: str
    files: dict
    rules: dict
    last: dict | None
    wants_json: bool
    static_reason: str | None   # this turn
    coverage: list              # this turn
    note: str                   # this turn
    reply: str                  # this turn
    data: dict | None           # this turn: the envelope for the data part
    pending: dict | None        # this turn: the pause to raise, if any
    route: str                  # this turn


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pause: Pending | None = None


class FixAgent:
    def __init__(self, checkpointer: Any, sandbox: Sandbox):
        self._checkpointer = checkpointer
        self.sandbox = sandbox

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._invoke(Scope(deps, Budget(), self.sandbox, incoming), context_id)

    async def resume(self, pending: Pending, answer: Answer, incoming: Incoming, context_id: str,
                     deps: TurnDeps) -> TurnResult:
        if answer.action == "cancel":
            return TurnResult(answer.reply)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACEPARENT_REPLY)
        scope = Scope(deps, Budget(), self.sandbox, incoming, resumed=(pending, answer))
        return await self._invoke(scope, context_id)

    async def _invoke(self, scope: Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"reply": "", "data": None, "pending": None, "route": "", "note": "", "coverage": [],
                 "static_reason": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pending = Pending.from_metadata(final.get("pending"))
        return TurnResult(final.get("reply") or prompts.BUDGET_SPENT, final.get("data"), pending)


def _build_graph(scope: Scope) -> StateGraph:
    async def intake_node(state: FixState) -> dict:
        if scope.resumed is not None:
            pending, answer = scope.resumed
            return intake.from_answer(pending, answer.action, answer.failure, answer.files)
        return intake.read_turn(dict(state), scope.incoming, scope.deps.traceparent)

    async def classify(state: FixState) -> dict:
        return await _guarded(classify_step(dict(state), scope))

    async def fix(state: FixState) -> dict:
        return await _guarded(fix_step(dict(state), scope))

    async def explain(state: FixState) -> dict:
        return await _guarded(explain_step(dict(state), scope))

    graph = StateGraph(FixState)
    for name, node in (("intake", intake_node), ("classify", classify), ("fix", fix), ("explain", explain)):
        graph.add_node(name, node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route, ["classify", "fix", "explain", END])
    graph.add_conditional_edges("classify", _route, ["fix", "explain", END])
    graph.add_edge("fix", END)
    graph.add_edge("explain", END)
    return graph


def _route(state: FixState) -> str:
    route = state.get("route") or ""
    return "fix" if route == "retry" else (route or END)


async def _guarded(step) -> dict:
    try:
        return await step
    except (TimeoutError, BudgetExhausted):
        log.warning("turn ran out of calls or time")
        return {"reply": prompts.BUDGET_SPENT, "route": ""}
