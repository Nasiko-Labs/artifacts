"""The kudos-collector LangGraph.

    START ─┬─ (packets were given in this context) → followup ─┬─ answered ───→ END
           │                                                   └─ NEEDS_PULL ─┐
           └─ (no packets yet) ────────────────────────────────→ collect ←────┘ → END

Before the graph, in code: a greeting gets the canned intro; a request to message or thank anyone gets
NO_SEND_NOTE; a request to rank or compare people gets RANKING_DECLINE. All three make zero tool and
LLM calls. A message that sets a new window, or pastes evidence, always collects afresh.

State is checkpointed per A2A contextId (thread_id) so a follow-up can be answered from the packets;
only the rendered packets and the envelope are kept (no raw tool output). The graph is compiled per
turn because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import pasted
import prompts
import render
import request
import smalltalk
import window
from collect import run
from deps import TurnDeps
from steps import ask_once

__all__ = ["KudosAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("kudos_collector.graph")


class KudosState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    report: str  # the last packets shown in this context: what follow-ups are answered from
    envelope: dict
    fresh: bool  # this turn's reply came from a collection (carries an envelope)


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


class KudosAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return TurnResult(canned)
        if request.asks_to_send(query):
            log.info("declined a request to message someone")
            return TurnResult(prompts.NO_SEND_NOTE)
        if request.asks_for_ranking(query):
            log.info("declined a ranking request")
            return TurnResult(prompts.RANKING_DECLINE)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        final = await graph.ainvoke({"query": query, "reply": "", "route": "", "fresh": False}, config,
                                    durability="exit")
        envelope = final.get("envelope") if final.get("fresh") else None
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, envelope)


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: KudosState) -> dict:
        return await _followup(state, deps)

    async def collect(state: KudosState) -> dict:
        outcome = await run(deps, state["query"])
        update = {"reply": outcome.reply, "route": "", "fresh": outcome.envelope is not None}
        if outcome.report:
            return {**update, "report": outcome.report, "envelope": outcome.envelope or {}}
        return update

    def start(state: KudosState) -> str:
        query = state["query"]
        if not state.get("report") or window.explicit(query.split("\n", 1)[0]) or pasted.has_paste(query):
            return "collect"
        return "followup"

    graph = StateGraph(KudosState)
    graph.add_node("followup", followup)
    graph.add_node("collect", collect)
    graph.add_conditional_edges(START, start, ["followup", "collect"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["collect", END])
    graph.add_edge("collect", END)
    return graph


async def _followup(state: KudosState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state['report'])}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "collect"}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.RANKING):
        return {"reply": prompts.RANKING_DECLINE, "route": ""}
    if render.judges_people(text):
        log.info("replaced a follow-up answer that ranked or judged a person")
        return {"reply": prompts.FOLLOWUP_REPLACED, "route": ""}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}
