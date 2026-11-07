"""The team-pulse LangGraph.

    START ─┬─ (cards were given in this context) → followup ─┬─ answered ───→ END
           │                                                 └─ NEEDS_PULL ─┐
           └─ (no cards yet) ─────────────────────────────────→ pull ←──────┘ → END

Before the graph, in code: a greeting gets the canned intro and a request to rank, rate, compare or
judge people gets a fixed decline (smalltalk.py, guardrails.py), both with zero tool and LLM calls.

State is checkpointed per A2A contextId (thread_id) so a follow-up can be answered from the cards;
only the rendered report is kept (no raw records, no tool output). The graph is compiled per turn
because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import guardrails
import prompts
import smalltalk
from deps import TurnDeps
from pulse import run_pulse
from steps import ask_once

__all__ = ["PulseAgent", "TurnDeps"]

log = logging.getLogger("team_pulse.graph")


class PulseState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    report: str  # the last cards shown in this context: what follow-ups are answered from


class PulseAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> str:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return canned
        if guardrails.asks_for_judgment(query):
            log.info("declined a ranking or judgment request")
            return prompts.RANKING_DECLINE
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke({"query": query, "reply": "", "route": ""}, config, durability="exit")
        return final.get("reply") or prompts.UNREADABLE_ANSWER


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: PulseState) -> dict:
        return await _followup(state, deps)

    async def pull(state: PulseState) -> dict:
        outcome = await run_pulse(deps, state["query"])
        update = {"reply": outcome.reply, "route": ""}
        return {**update, "report": outcome.report} if outcome.report else update

    graph = StateGraph(PulseState)
    graph.add_node("followup", followup)
    graph.add_node("pull", pull)
    graph.add_conditional_edges(START, lambda s: "followup" if s.get("report") else "pull", ["followup", "pull"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["pull", END])
    graph.add_edge("pull", END)
    return graph


async def _followup(state: PulseState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state['report'])}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "pull"}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.RANKING):
        return {"reply": prompts.RANKING_DECLINE, "route": ""}
    if guardrails.has_judgment_words(text):
        log.info("replaced a follow-up answer that judged a person")
        return {"reply": prompts.FOLLOWUP_REPLACED, "route": ""}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}
