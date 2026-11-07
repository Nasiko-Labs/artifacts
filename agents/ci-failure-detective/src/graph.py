"""The CI failure detective LangGraph.

    START ─┬─ (a report exists, no new target named) → followup ─┬─ answered ────────→ END
           │                                                     ├─ POST / TICKET → write → END
           │                                                     └─ RECHECK / OTHER_JOB ─┐
           └─ intake ─┬─ asked / declined → END                                          │
                      └─ locate → log → gather (commits ∥ history) → blame → verdict → render → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId, so a follow-up sees the last report and the repository named
earlier. The graph is compiled per turn because its nodes close over that turn's dependencies; the
checkpointer is shared. A write proposal leaves the graph in the turn result; the approve → write walk
runs outside it, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import pipeline
import smalltalk
from deps import TurnDeps
from writes import Proposal

__all__ = ["DetectiveAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("ci_detective.graph")


class DetectiveState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    repo: str                  # remembered for this conversation
    pending: dict              # a pull request named without a repository: asked once
    target: dict               # this turn's target
    report: dict               # the last diagnosis: what follow-ups and writes use
    outcomes: dict[str, str]   # write key → sent / skipped / denied / blocked / not_sent
    write: str                 # this turn: "comment" | "ticket"
    proposal: dict             # this turn: a write ready for the approval walk


@dataclass(frozen=True)
class TurnResult:
    reply: str
    proposal: Proposal | None = None


class DetectiveAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "target": {}, "write": "", "proposal": {}}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        proposal = final.get("proposal")
        return TurnResult(final.get("reply") or "", Proposal.from_dict(proposal) if proposal else None)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember each write's outcome, so a second identical write on the same run is refused."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "proposal": {}}, as_node="render")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    run = pipeline.Pipeline(deps)

    async def intake_node(state: DetectiveState) -> dict:
        return await intake.intake(deps, state)

    async def followup_node(state: DetectiveState) -> dict:
        return await intake.followup(deps, state)

    async def write_node(state: DetectiveState) -> dict:
        return await intake.write(deps, state)

    graph = StateGraph(DetectiveState)
    nodes = (("intake", intake_node), ("followup", followup_node), ("write", write_node),
             ("locate", run.locate), ("log", run.log), ("gather", run.gather), ("blame", run.blame),
             ("verdict", run.verdict), ("render", run.render))
    for name, node in nodes:
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["intake", "followup"])
    graph.add_conditional_edges("intake", _route, ["locate", END])
    graph.add_conditional_edges("followup", _route, ["locate", "write", END])
    for a, b in (("locate", "log"), ("log", "gather"), ("gather", "blame"), ("blame", "verdict"), ("verdict", "render")):
        graph.add_conditional_edges(a, _route_to(b), [b, END])
    graph.add_edge("render", END)
    graph.add_edge("write", END)
    return graph


def _entry(state: DetectiveState) -> str:
    if state.get("report") and not intake.names_new_target(state):
        return "followup"
    return "intake"


def _route(state: DetectiveState) -> str:
    return state.get("route") or END


def _route_to(nxt: str):
    def route(state: DetectiveState) -> str:
        return nxt if state.get("route") == "next" else END
    return route
