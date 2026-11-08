"""The weekly-leadership-rollup LangGraph.

    START ─┬─ (a rollup was given in this context) → followup ─┬─ answered ───→ END
           │                                                    └─ NEEDS_PULL ─┐
           └─ (no rollup yet, or teams were just chosen) ────────→ pull ←──────┘ → END

Before the graph, in code: a greeting gets the canned intro and an ask to post or send the rollup gets a
fixed decline (copy-only), both with zero tool and LLM calls.

State is checkpointed per A2A contextId (thread_id) so a follow-up can be answered from the stored rollup;
only the rendered rollup is kept (no raw records, no tool output). The graph is compiled per turn because
its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import request
import smalltalk
from deps import TurnDeps
from pauses import Pending, chosen_teams
from steps import ask_once
from turn import run_rollup

__all__ = ["RollupAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("weekly_rollup.graph")


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pause: Pending | None = None


class RollupState(TypedDict, total=False):
    query: str
    wants_json: bool
    teams: list
    reply: str
    route: str
    data: dict
    pause: list
    report: str  # the last rollup shown in this context: what follow-ups are answered from


class RollupAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, wants_json: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return TurnResult(canned)
        if request.wants_post(query):
            log.info("declined an ask to post the rollup")
            return TurnResult(prompts.POST_DECLINE)
        return await self._run(query, context_id, deps, wants_json, ())

    async def resume(self, pending: Pending, answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        teams = chosen_teams(pending, answer)
        if not teams:
            return TurnResult(prompts.TEAMS_NOT_MATCHED)
        return await self._run(pending.query, context_id, deps, pending.wants_json, teams)

    async def _run(self, query: str, context_id: str, deps: TurnDeps, wants_json: bool,
                   teams: tuple[str, ...]) -> TurnResult:
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        start = {"query": query, "wants_json": wants_json, "teams": list(teams), "reply": "", "route": "",
                 "data": {}, "pause": []}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(start, config, durability="exit")
        if final.get("pause"):
            return TurnResult(final.get("reply") or "", None, Pending(query, tuple(final["pause"]), wants_json))
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("data") or None)


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: RollupState) -> dict:
        return await _followup(state, deps)

    async def pull(state: RollupState) -> dict:
        req = request.parse(state["query"], deps.today, state.get("wants_json", False))
        outcome = await run_rollup(deps, req, tuple(state.get("teams") or ()))
        update = {"reply": outcome.reply, "route": "", "data": outcome.data or {}, "pause": list(outcome.pause_teams)}
        return {**update, "report": outcome.report} if outcome.report else update

    def first(state: RollupState) -> str:
        return "followup" if state.get("report") and not state.get("teams") else "pull"

    graph = StateGraph(RollupState)
    graph.add_node("followup", followup)
    graph.add_node("pull", pull)
    graph.add_conditional_edges(START, first, ["followup", "pull"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["pull", END])
    graph.add_edge("pull", END)
    return graph


async def _followup(state: RollupState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    if request.parse(state["query"], deps.today).unreported:
        return {"route": "pull"}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state['report'])}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "pull"}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}
