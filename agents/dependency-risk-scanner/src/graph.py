"""The dependency risk scanner LangGraph.

    START ─┬─ (a report exists, no new advisory, manifests or repositories) → followup ─┬─ answered ─→ END
           │                                                                            └─ draft → END
           └─ intake ─┬─ asked / declined → END                                          (plan → walk)
                      └─ scan (repos → manifests → parse + match → summary → render) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId, so a follow-up sees the last report and a pending question. The
graph is compiled per turn because its nodes close over that turn's dependencies; the checkpointer is
shared. A PR plan leaves the graph in the turn result; the card walk runs outside it, its state on the
A2A task.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import smalltalk
import steps
from deps import TurnDeps

__all__ = ["ScannerAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("dependency_risk.graph")


class ScannerState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    pending: dict         # a question waiting for its answer: the advisory so far and what to scan
    ask: bool             # this turn asked a question
    ask_header: str
    job: dict             # this turn's scan: advisory, repos, owner, pasted manifests
    report: dict          # the last report: what follow-ups and PRs use
    envelope: dict        # this turn's envelope
    pr_repos: list        # this turn: repositories to open PRs for
    proposal: dict        # this turn: a plan ready for the card walk


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    proposal: dict | None = None
    ask: bool = False
    ask_header: str = ""


class ScannerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "envelope": {}, "proposal": {}, "ask": False,
                 "ask_header": "", "job": {}, "pr_repos": []}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("envelope") or None, final.get("proposal") or None,
                          bool(final.get("ask")), final.get("ask_header") or "")


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def intake_node(state: ScannerState) -> dict:
        return await steps.intake_node(deps, state)

    async def scan_node(state: ScannerState) -> dict:
        return await steps.scan_node(deps, state)

    async def followup_node(state: ScannerState) -> dict:
        return await steps.followup_node(deps, state)

    async def draft_node(state: ScannerState) -> dict:
        return await steps.draft_node(deps, state)

    graph = StateGraph(ScannerState)
    for name, node in (("intake", intake_node), ("scan", scan_node), ("followup", followup_node),
                       ("draft", draft_node)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["intake", "followup"])
    graph.add_conditional_edges("intake", _route, ["scan", END])
    graph.add_conditional_edges("followup", _route, ["draft", END])
    graph.add_edge("scan", END)
    graph.add_edge("draft", END)
    return graph


def _entry(state: ScannerState) -> str:
    report = state.get("report") or {}
    repos = tuple((report.get("scan") or {}).get("repos") or ())
    if report and not state.get("pending") and not intake.is_new_scan(state.get("query") or "", repos):
        return "followup"
    return "intake"


def _route(state: ScannerState) -> str:
    return state.get("route") or END
