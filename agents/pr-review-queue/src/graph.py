"""The PR review queue LangGraph.

    START ─┬─ (a queue exists and no new repository set is named) → followup ─→ END
           └─ intake (parse → off-topic / ask repos / read → compute → render) ───→ END

A greeting or small talk never enters the graph (smalltalk.py); a request without a traceparent gets a
fixed reply before any call. State is checkpointed per A2A contextId, so follow-ups recompute from the
stored snapshot. Nudge proposals leave the graph in the turn result; the approve → write walk runs
outside it, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import prompts
import smalltalk
from deps import TurnDeps
from followups import followup_turn
from nudge import Proposal
from turn import Step, intake_turn
from view import Settings, Snapshot

log = logging.getLogger("pr_review_queue.graph")


class QueueState(TypedDict, total=False):
    query: str
    reply: str
    data: dict
    proposals: list
    ask_repos: bool
    settings: dict             # remembered repositories and thresholds for this conversation
    snapshot: dict             # the last read of the code host (or paste)
    outcomes: dict[str, str]   # nudge key → sent / skipped / denied / blocked / not_sent


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    proposals: tuple[Proposal, ...] = ()
    ask_repos: bool = False


class QueueAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "data": {}, "proposals": [], "ask_repos": False}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        proposals = tuple(Proposal.from_dict(p) for p in final.get("proposals") or ())
        return TurnResult(final.get("reply") or "", final.get("data") or None, proposals,
                          bool(final.get("ask_repos")))

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember each nudge's outcome, so the same pull request is not nudged twice."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "proposals": []}, as_node="intake")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _apply(step: Step) -> dict:
    out = {"reply": step.reply, "data": step.data or {}, "proposals": [p.to_dict() for p in step.proposals],
           "ask_repos": step.ask_repos}
    return {**out, **step.remember}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def intake_node(state: QueueState) -> dict:
        return _apply(await intake_turn(deps, state["query"], Settings.from_dict(state.get("settings"))))

    async def followup_node(state: QueueState) -> dict:
        step = await followup_turn(deps, state["query"], Settings.from_dict(state.get("settings")),
                                   Snapshot.from_dict(state.get("snapshot")), state.get("outcomes") or {})
        return _apply(step)

    graph = StateGraph(QueueState)
    graph.add_node("intake", intake_node)
    graph.add_node("followup", followup_node)
    graph.add_conditional_edges(START, _entry, ["intake", "followup"])
    graph.add_edge("intake", END)
    graph.add_edge("followup", END)
    return graph


def _entry(state: QueueState) -> str:
    query = state.get("query") or ""
    if not state.get("snapshot") or ('"number"' in query and '"title"' in query):
        return "intake"  # no queue yet, or a pasted pull request list
    known = set(Settings.from_dict(state.get("settings")).repos)
    named = set(intake.parse(query).repos)
    return "intake" if named and not named <= known else "followup"
