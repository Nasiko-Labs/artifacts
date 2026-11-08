"""The security-gate LangGraph.

    START ─┬─ (a report exists, nothing new named or pasted) → followup ─┬─ answered ─────→ END
           │                                                            ├─ post → write ──→ END
           │                                                            └─ rescan → pr ───→ END
           └─ intake ─┬─ declined / asked / off-topic ──→ END
                      ├─ pasted diff, code or findings → pasted → END
                      └─ pull request → pr (pr → files → scan → map → explain → verdict) → END

A greeting or small talk never enters the graph (smalltalk.py). State is checkpointed per A2A contextId,
so a follow-up sees the last report and the repository named earlier. The graph is compiled per turn
because its nodes close over that turn's dependencies; the checkpointer is shared. A write proposal leaves
the graph in the turn result; the approve → write walk runs outside it, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import conversation
import pipeline
import request
import smalltalk
from deps import TurnDeps
from records import GateReport
from request import Asked
from scanner import Runner
from writes import Proposal

__all__ = ["GateAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("security_gate.graph")


class GateState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    json_mode: bool
    repo: str                  # remembered for this conversation
    pending: dict              # a PR number named without a repository: asked once
    asked: dict                # this turn's parsed request
    report: dict               # the last report: what follow-ups and the write use
    outcomes: dict[str, str]   # write key → sent / skipped / denied / blocked / not_sent
    proposal: dict
    envelope: dict


@dataclass(frozen=True)
class TurnResult:
    reply: str
    proposal: Proposal | None = None
    envelope: dict | None = None


class GateAgent:
    def __init__(self, checkpointer: Any, runner: Runner | None = None):
        self._checkpointer = checkpointer
        self._runner = runner

    async def turn(self, query: str, context_id: str, deps: TurnDeps, json_mode: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps, self._runner).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "asked": {}, "proposal": {}, "envelope": {},
                 "json_mode": json_mode or request.wants_json(query)}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        proposal = final.get("proposal")
        return TurnResult(final.get("reply") or "", Proposal.from_dict(proposal) if proposal else None,
                          final.get("envelope") or None)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember each write's outcome, so a second identical write is refused."""
        if not outcomes:
            return
        graph = _build_graph(None, None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "proposal": {}}, as_node="write")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _asked(state: GateState) -> Asked:
    return Asked(**(state.get("asked") or {}))


def _build_graph(deps: TurnDeps | None, runner: Runner | None) -> StateGraph:
    async def intake_node(state: GateState) -> dict:
        return await conversation.intake(deps, state)

    async def followup_node(state: GateState) -> dict:
        return await conversation.followup(deps, state)

    async def write_node(state: GateState) -> dict:
        return await conversation.write(deps, state)

    async def pr_node(state: GateState) -> dict:
        return _finish(state, await pipeline.run_pr(deps, _asked(state), runner))

    async def pasted_node(state: GateState) -> dict:
        return _finish(state, await pipeline.run_pasted(deps, state["query"], _asked(state), runner))

    graph = StateGraph(GateState)
    for name, node in (("intake", intake_node), ("followup", followup_node), ("write", write_node),
                       ("pr", pr_node), ("pasted", pasted_node)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["intake", "followup"])
    graph.add_conditional_edges("intake", _route, ["pr", "pasted", END])
    graph.add_conditional_edges("followup", _route, ["pr", "write", END])
    for name in ("pr", "pasted", "write"):
        graph.add_edge(name, END)
    return graph


def _finish(state: GateState, result: pipeline.Result) -> dict:
    if result.report is None:
        return {"reply": result.reply, "route": ""}
    report: GateReport = result.report
    log.info("report: %s, %d findings, confidence %s", report.verdict, len(report.findings), report.confidence)
    return conversation.report_reply(state, report, _asked(state).include_existing)


def _entry(state: GateState) -> str:
    if state.get("report") and not conversation.names_new_input(state):
        return "followup"
    return "intake"


def _route(state: GateState) -> str:
    return state.get("route") or END
