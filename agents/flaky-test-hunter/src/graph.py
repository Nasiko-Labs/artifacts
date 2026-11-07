"""The flaky test hunter LangGraph.

    START ─┬─ (a report exists, no new repository or pasted logs) → followup ─┬─ answered ──────→ END
           │                                                                  ├─ draft → END (plan → walk)
           │                                                                  └─ rescan ─┐
           └─ intake ─┬─ asked / declined → END                                          │
                      └─ scan (runs → logs → commits → rank → headline → render) ◀───────┘ → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId, so a follow-up sees the last report and the parameters named
earlier. The graph is compiled per turn because its nodes close over that turn's dependencies; the
checkpointer is shared. A draft plan leaves the graph in the turn result; the four-card walk runs outside
it, its state on the A2A task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import pasted
import smalltalk
import turns
from deps import TurnDeps

__all__ = ["HunterAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("flaky_hunter.graph")


class HunterState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    params: dict          # remembered for this conversation: repo, workflow, branch, runs
    pending: dict         # parameters waiting for the repository the user was asked for
    ask: bool             # this turn asked for the repository
    scan_params: dict     # this turn's scan
    pasted: bool
    report: dict          # the last report: what follow-ups and drafts use
    envelope: dict        # this turn's envelope
    rank: int
    skip_ticket: bool
    proposal: dict        # this turn: a plan ready for the four-card walk


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    proposal: dict | None = None
    ask: bool = False


class HunterAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "envelope": {}, "proposal": {}, "ask": False,
                 "scan_params": {}, "pasted": False}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("envelope") or None, final.get("proposal") or None,
                          bool(final.get("ask")))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def intake_node(state: HunterState) -> dict:
        return await turns.intake_node(deps, state)

    async def scan_node(state: HunterState) -> dict:
        return await turns.scan_node(deps, state)

    async def followup_node(state: HunterState) -> dict:
        return await turns.followup_node(deps, state)

    async def draft_node(state: HunterState) -> dict:
        return await turns.draft_node(deps, state)

    graph = StateGraph(HunterState)
    for name, node in (("intake", intake_node), ("scan", scan_node), ("followup", followup_node),
                       ("draft", draft_node)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["intake", "followup"])
    graph.add_conditional_edges("intake", _route, ["scan", END])
    graph.add_conditional_edges("followup", _route, ["scan", "draft", END])
    graph.add_edge("scan", END)
    graph.add_edge("draft", END)
    return graph


def _entry(state: HunterState) -> str:
    query = state.get("query") or ""
    remembered = (state.get("params") or {}).get("repo") or ""
    if state.get("report") and not state.get("pending") and not pasted.has_runs(query) \
            and not intake.names_new_repo(query, remembered) and intake.envelope_params(query) is None:
        return "followup"
    return "intake"


def _route(state: HunterState) -> str:
    return state.get("route") or END
