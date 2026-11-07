"""The context-aware PR reviewer LangGraph.

    START ─┬─ (answer to "which ticket?") ─────────────────────────────────────→ review ─→ END
           ├─ (a review exists, no new PR named) → followup ─┬─ answered ────────────→ END
           │                                                 ├─ POST → write ────────→ END
           │                                                 └─ other ticket → review → END
           └─ intake ─┬─ asked / declined ──→ END
                      ├─ pasted diff ────→ pasted → END
                      └─ review (pr → diff ∥ ticket → similar → precedent → criteria → review → align) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId, so a follow-up sees the last review and the repository named
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
import render
import smalltalk
import targets
from deps import TurnDeps
from targets import Target
from writes import Proposal

__all__ = ["ReviewerAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("pr_reviewer.graph")


class ReviewerState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    json_mode: bool
    ticket_answer: bool        # this turn answers the "which ticket?" question
    repo: str                  # remembered for this conversation
    pending: dict              # a pull request named without a repository: asked once
    pending_ticket: dict       # a pull request waiting for its ticket
    target: dict
    report: dict               # the last review: what follow-ups and the write use
    outcomes: dict[str, str]   # write key → sent / skipped / denied / blocked / not_sent
    proposal: dict
    question: dict
    envelope: dict


@dataclass(frozen=True)
class TurnResult:
    reply: str
    proposal: Proposal | None = None
    question: dict | None = None
    envelope: dict | None = None


class ReviewerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, ticket_answer: bool = False) -> TurnResult:
        canned = None if ticket_answer else smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "target": {}, "proposal": {}, "question": {},
                 "envelope": {}, "ticket_answer": ticket_answer, "json_mode": targets.wants_json(query)}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        proposal = final.get("proposal")
        return TurnResult(final.get("reply") or "", Proposal.from_dict(proposal) if proposal else None,
                          final.get("question") or None, final.get("envelope") or None)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember each write's outcome, so a second identical write is refused."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "proposal": {}}, as_node="write")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def intake_node(state: ReviewerState) -> dict:
        return await intake.intake(deps, state)

    async def followup_node(state: ReviewerState) -> dict:
        return await intake.followup(deps, state)

    async def answer_node(state: ReviewerState) -> dict:
        return intake.answer_ticket(state)

    async def write_node(state: ReviewerState) -> dict:
        return await intake.write(deps, state)

    async def review_node(state: ReviewerState) -> dict:
        target = Target.from_dict(state.get("target"))
        if target is None:
            return {"reply": "I lost track of which pull request to review. Please name it again.", "route": ""}
        return _finish(state, target, await pipeline.run(deps, target))

    async def pasted_node(state: ReviewerState) -> dict:
        return _finish(state, None, await pipeline.run_pasted(deps, state["query"], targets.focus_in(state["query"])))

    graph = StateGraph(ReviewerState)
    for name, node in (("intake", intake_node), ("followup", followup_node), ("answer", answer_node),
                       ("write", write_node), ("review", review_node), ("pasted", pasted_node)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["intake", "followup", "answer"])
    graph.add_conditional_edges("intake", _route, ["review", "pasted", END])
    graph.add_conditional_edges("answer", _route, ["review", END])
    graph.add_conditional_edges("followup", _route, ["review", "write", END])
    for name in ("review", "pasted", "write"):
        graph.add_edge(name, END)
    return graph


def _finish(state: ReviewerState, target: Target | None, result: pipeline.Result) -> dict:
    if result.question:
        return {"reply": "", "question": result.question, "pending_ticket": target.to_dict() if target else {},
                "route": ""}
    if result.review is None:
        return {"reply": result.reply, "route": ""}
    review = result.review
    envelope = render.envelope(review)
    reply = render.as_json(envelope) if state.get("json_mode") else render.card(review)
    log.info("reviewed %s: %s %s", review.pr_label, review.label, review.coverage)
    out = {"reply": reply, "report": review.to_dict(), "envelope": envelope, "route": ""}
    return {**out, "repo": review.repo_full} if review.repo_full else out


def _entry(state: ReviewerState) -> str:
    if state.get("ticket_answer") and state.get("pending_ticket"):
        return "answer"
    if state.get("report") and not intake.names_new_target(state) and "diff --git " not in state.get("query", ""):
        return "followup"
    return "intake"


def _route(state: ReviewerState) -> str:
    return state.get("route") or END
