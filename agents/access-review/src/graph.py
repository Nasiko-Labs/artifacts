"""The access-review LangGraph.

    START ─┬─ (a review exists, no new paste or organization, no question pending) → followup → END
           └─ intake ─┬─ fixed reply / question → END
                      └─ review (parse → code-host read → match → rules → drafts → headline → render) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId and keeps the last review's findings and drafts, the policy and
the typed login mappings; never the raw paste. The graph is compiled per turn because its nodes close
over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import smalltalk
import steps
from deps import TurnDeps

__all__ = ["AccessReviewAgent", "TurnDeps", "TurnResult"]


class ReviewState(TypedDict, total=False):
    reply: str
    route: str
    pending: dict      # the organization question: parsed rows wait here until it is answered
    ask: bool
    ask_header: str
    review: dict       # the last review: findings, drafts, counts (no raw rows)
    envelope: dict     # this turn's envelope
    policy: dict       # the policy in force for this conversation
    mappings: dict     # login → email, typed by the user


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    ask: bool = False
    ask_header: str = ""


class AccessReviewAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, as_json: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        # The message (which may hold a pasted export) is never part of the checkpointed state: the nodes
        # read it from this turn's closure, so only findings, drafts and counts are stored.
        graph = _build_graph(deps, query, as_json).compile(checkpointer=self._checkpointer)
        fresh = {"reply": "", "route": "", "envelope": {}, "ask": False, "ask_header": ""}
        final = await graph.ainvoke(fresh, config(context_id), durability="exit")
        return TurnResult(final.get("reply") or "", final.get("envelope") or None, bool(final.get("ask")),
                          final.get("ask_header") or "")

    async def state(self, context_id: str) -> dict:
        graph = _build_graph(None, "", False).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(config(context_id))).values)


def config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None, query: str, as_json: bool) -> StateGraph:
    async def intake_node(state: ReviewState) -> dict:
        return await steps.intake_node(deps, {**state, "query": query, "as_json": as_json})

    async def followup_node(state: ReviewState) -> dict:
        return await steps.followup_node(deps, {**state, "query": query})

    def entry(state: ReviewState) -> str:
        return _entry(state, query)

    graph = StateGraph(ReviewState)
    graph.add_node("intake", intake_node)
    graph.add_node("followup", followup_node)
    graph.add_conditional_edges(START, entry, ["intake", "followup"])
    graph.add_edge("intake", END)
    graph.add_edge("followup", END)
    return graph


def _entry(state: ReviewState, query: str) -> str:
    if not state.get("review") or (state.get("pending") or {}).get("stage"):
        return "intake"
    request = intake.read(query)
    return "intake" if request.has_data or request.org else "followup"
