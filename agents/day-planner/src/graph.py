"""The day-planner LangGraph.

    START ─┬─ (a timezone answer is pending) → resume ──────────────────────────→ END
           ├─ (a plan was shown in this context) → followup ─┬─ answered ───────→ END
           │                                                 └─ replan ─┐
           └─ (no plan yet) ─────────────────────────────────────────→ plan ───→ END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id): the last plan (for follow-ups), the zone and the
working hours, and the pending timezone question. The graph is compiled per turn because its nodes close
over that turn's dependencies (LLM client, gateway, inbound traceparent); the checkpointer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup as fu
import planner
import smalltalk
from deps import TurnDeps
from models import Plan

__all__ = ["DayPlannerAgent", "TurnDeps", "TurnResult"]


class PlannerState(TypedDict, total=False):
    query: str
    resuming: bool
    reply: str
    route: str
    envelope: dict | None
    question: dict | None
    plan: dict | None          # the last plan shown: what follow-ups answer from
    zone: dict | None          # timewin.Zone.to_dict(), remembered for the conversation
    hours: list[str] | None    # working hours stated in this conversation
    pending: dict | None       # the fetched day, kept until the timezone question is answered


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    question: dict | None = None


class DayPlannerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        return await self._run({"query": query, "resuming": False}, context_id, deps)

    async def resume(self, answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to a paused task (the timezone question)."""
        return await self._run({"query": answer, "resuming": True}, context_id, deps)

    async def _run(self, values: dict, context_id: str, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {**values, "reply": "", "route": "", "envelope": None, "question": None}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return TurnResult(final.get("reply") or "", final.get("envelope"), final.get("question"))


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def resume(state: PlannerState) -> dict:
        outcome = await planner.resume(deps, state["query"], state.get("pending") or {})
        if outcome is None:  # not a timezone: a new message
            return {"pending": None, "route": "plan" if not state.get("plan") else "followup"}
        return _apply(outcome, state)

    async def followup(state: PlannerState) -> dict:
        result = await fu.answer(deps, state["query"], Plan.from_dict(state["plan"]))
        return {"route": "plan"} if result.replan else {"reply": result.reply, "route": ""}

    async def plan(state: PlannerState) -> dict:
        settings = planner.Settings(zone=state.get("zone"), hours=tuple(state["hours"]) if state.get("hours") else None)
        return _apply(await planner.plan_turn(deps, state["query"], settings), state)

    graph = StateGraph(PlannerState)
    for name, node in (("resume", resume), ("followup", followup), ("plan", plan)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["resume", "followup", "plan"])
    graph.add_conditional_edges("resume", _route, ["followup", "plan", END])
    graph.add_conditional_edges("followup", _route, ["plan", END])
    graph.add_edge("plan", END)
    return graph


def _entry(state: PlannerState) -> str:
    if state.get("resuming") and state.get("pending"):
        return "resume"
    return "followup" if state.get("plan") else "plan"


def _route(state: PlannerState) -> str:
    return state.get("route") or END


def _apply(outcome: planner.Outcome, state: PlannerState) -> dict:
    """The outcome as a state update: a new plan replaces the old one; a question keeps the old one."""
    update: dict = {"reply": outcome.reply, "envelope": outcome.envelope, "question": outcome.question,
                    "pending": outcome.pending, "route": ""}
    if outcome.plan is not None:
        update["plan"] = outcome.plan.to_dict()
    if outcome.zone is not None:
        update["zone"] = outcome.zone
    if outcome.hours is not None:
        update["hours"] = list(outcome.hours)
    return update
