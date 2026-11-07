"""The onboarding-guide LangGraph.

    START ─┬─ (guide stored + a follow-up: commands only / shorter / full guide / who owns <path> /
           │   focus on <path> / add a section on <x>) → followup (code only) → END
           └─ otherwise ───────────────────────────────────────────────────────→ build (pipeline.py) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id): the last guide, so follow-ups answer from it with
zero tool and LLM calls. The graph is compiled per turn because its nodes close over that turn's
dependencies (LLM client, gateway, inbound traceparent); the checkpointer is shared. The one pause (no
repository named) leaves the graph in the turn result; its answer comes back through `resume_repo`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import intake
import pipeline
import prompts
import render
import smalltalk
from deps import TurnDeps
from guide import Guide
from reader import TurnBudget

__all__ = ["OnboardingAgent", "TurnResult"]


class GuideState(TypedDict, total=False):
    query: str
    reply: str
    guide: dict | None        # the last guide built in this conversation
    pause: dict | None        # this turn: a question to pause on
    envelope: dict | None     # this turn: the envelope of the guide shown
    asked_repo: bool


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: pipeline.Pause | None = None
    envelope: dict | None = None
    budget: TurnBudget | None = None


class OnboardingAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, asked_repo: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        budget = TurnBudget()
        graph = _build_graph(deps, budget).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "pause": None, "envelope": None, "asked_repo": asked_repo}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pause = final.get("pause")
        return TurnResult(final.get("reply") or "", pipeline.Pause(**pause) if pause else None,
                          final.get("envelope") or None, budget)

    async def resume_repo(self, answer: str, asked: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """The repository question's answer, read together with what was first asked; never asked twice."""
        return await self.turn(f"{asked}\nRepository: {answer}".strip(), context_id, deps, asked_repo=True)


def _build_graph(deps: TurnDeps, budget: TurnBudget) -> StateGraph:
    async def followup(state: GuideState) -> dict:
        return _followup(state)

    async def build(state: GuideState) -> dict:
        outcome = await pipeline.build(deps, state["query"], budget, asked_repo=bool(state.get("asked_repo")))
        pause = outcome.pause
        update: dict = {"reply": outcome.reply, "envelope": outcome.envelope,
                        "pause": {"kind": pause.kind, "text": pause.text, "asked": pause.asked} if pause else None}
        if outcome.guide is not None:
            update["guide"] = outcome.guide.to_dict()
        return update

    graph = StateGraph(GuideState)
    graph.add_node("followup", followup)
    graph.add_node("build", build)
    graph.add_conditional_edges(START, _entry, ["followup", "build"])
    graph.add_edge("followup", END)
    graph.add_edge("build", END)
    return graph


def _entry(state: GuideState) -> str:
    query = state.get("query") or ""
    follow = intake.follow_up(query)
    if follow is None:
        return "build"
    if follow.kind in ("owners", "focus") and (not state.get("guide") or intake.find_repo(query)):
        return "build"  # no guide yet, or a new repository named: build one (asking for the repo if needed)
    return "followup"


def _followup(state: GuideState) -> dict:
    """Answered from the stored guide: zero tool and LLM calls."""
    stored = state.get("guide")
    if not stored:
        return {"reply": prompts.NO_GUIDE_YET, "envelope": None, "pause": None}
    guide = Guide.from_dict(stored)
    follow = intake.follow_up(state["query"])
    views = {"commands": lambda: render.render_commands(guide), "shorter": lambda: render.render_short(guide),
             "full": lambda: render.render(guide), "owners": lambda: render.render_owners(guide, follow.arg),
             "focus": lambda: render.render_focus(guide, follow.arg),
             "section": lambda: render.render_section(guide, follow.arg)}
    reply = views[follow.kind]()
    return {"reply": reply, "envelope": render.envelope(guide, reply), "pause": None}
