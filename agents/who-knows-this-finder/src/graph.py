"""The who-knows LangGraph.

    START ─┬─ (evidence stored + "more" / "why <name>") → followup (code only) → END
           └─ otherwise ─────────────────────────────────→ find (pipeline.py) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id): the last search's gathered evidence, so MORE and
WHY answer from it with zero tool and LLM calls. The graph is compiled per turn because its nodes close
over that turn's dependencies (LLM client, gateway, inbound traceparent); the checkpointer is shared.
A pause (vague topic, repository choice) leaves the graph in the turn result; its answer comes back
through `resume_topic` / `resume_repo`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import pipeline
import render
import smalltalk
from deps import TurnDeps
from finding import Gathered, build, choose_repo
from reader import TurnBudget
from request import is_more, why_whom

__all__ = ["TurnResult", "WhoKnowsAgent"]


class WhoKnowsState(TypedDict, total=False):
    query: str
    reply: str
    gathered: dict | None     # the last search's evidence: what MORE / WHY / a repo answer read
    pause: dict | None        # this turn: a question to pause on
    envelope: dict | None     # this turn: the envelope of a list shown
    allow_vague: bool


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pipeline.Pause | None = None
    envelope: dict | None = None
    budget: TurnBudget | None = None


class WhoKnowsAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, allow_vague: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        budget = TurnBudget()
        graph = _build_graph(deps, budget).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "pause": None, "envelope": None, "allow_vague": allow_vague}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        pause = final.get("pause")
        return TurnResult(final.get("reply") or "", pipeline.Pause(**pause) if pause else None,
                          final.get("envelope") or None, budget)

    async def resume_topic(self, answer: str, asked: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """The vague-topic question's answer, read together with what was first asked; never asked twice."""
        return await self.turn(f"{asked} {answer}".strip(), context_id, deps, allow_vague=True)

    async def resume_repo(self, answer: str, context_id: str) -> TurnResult:
        """The repository picked on the question: code evidence narrowed to it, zero tool or LLM calls."""
        values = await self._values(context_id)
        stored = values.get("gathered")
        if not stored:
            return TurnResult("I no longer have that search. Please ask again.")
        outcome = pipeline.reply_for(choose_repo(Gathered.from_dict(stored), answer))
        await self._save(context_id, {"gathered": outcome.gathered.to_dict(), "pause": None})
        return TurnResult(outcome.reply, envelope=outcome.envelope)

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None, None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)

    async def _save(self, context_id: str, values: Mapping[str, Any]) -> None:
        graph = _build_graph(None, None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), dict(values), as_node="find")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None, budget: TurnBudget | None) -> StateGraph:
    async def followup(state: WhoKnowsState) -> dict:
        return _followup(state)

    async def find(state: WhoKnowsState) -> dict:
        prior = (state.get("gathered") or {}).get("topic") or ""
        outcome = await pipeline.find(deps, state["query"], budget, allow_vague=bool(state.get("allow_vague")),
                                      prior_topic=prior)
        update: dict = {"reply": outcome.reply, "envelope": outcome.envelope, "pause": _pause_state(outcome.pause)}
        if outcome.gathered is not None:
            update["gathered"] = outcome.gathered.to_dict()
        return update

    graph = StateGraph(WhoKnowsState)
    graph.add_node("followup", followup)
    graph.add_node("find", find)
    graph.add_conditional_edges(START, _entry, ["followup", "find"])
    graph.add_edge("followup", END)
    graph.add_edge("find", END)
    return graph


def _entry(state: WhoKnowsState) -> str:
    query = state.get("query") or ""
    if state.get("gathered") and (is_more(query) or why_whom(query) is not None):
        return "followup"
    return "find"


def _followup(state: WhoKnowsState) -> dict:
    """MORE and WHY, from the stored evidence: zero tool and LLM calls."""
    finding = build(Gathered.from_dict(state["gathered"]))
    query = state["query"]
    who = why_whom(query)
    reply = render.render_more(finding) if who is None else render.render_why(finding, who)
    return {"reply": reply, "envelope": render.envelope(finding), "pause": None}


def _pause_state(pause: pipeline.Pause | None) -> dict | None:
    if pause is None:
        return None
    return {"kind": pause.kind, "text": pause.text, "hitl": pause.hitl, "topic": pause.topic}
