"""The slo-burn-forecaster LangGraph.

    START -+- (a forecast in this context, no new data) -> followup -+- WHAT IF / ONLY / SLO / NUMBERS / WHY -> END
           |                                                       +- NEW_RUN -> forecast
           +- (new data, an SLO answer, or nothing stored yet) ------> forecast -> END

forecast = parse rows (code) -> SLO targets (code; one model read only when code finds a target-like phrase it
cannot parse) -> one SLO question when a service still has no target -> burn + rank (code) -> optional
corroboration for the top two burners (read-only tool loops) -> headline (one model call, checked) -> render.
A greeting never enters the graph (smalltalk.py). No model call ever computes a number.

State is checkpointed per A2A contextId, so a follow-up is answered from the stored rows. The graph is compiled
per turn because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import intake
import prompts
import request
import smalltalk
from deps import TurnDeps
from llm import ask_once
from models import Run
from outcome import finish

__all__ = ["SLO_KEY", "ForecastAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("slo_burn.graph")

TURN_DEADLINE_SECS = 330
SLO_KEY = intake.SLO_KEY
OUT_OF_TIME = "I ran out of time before finishing this forecast. Please try again."


class ForecastState(TypedDict, total=False):
    query: str
    json_mode: bool
    answer: str | None         # this turn: the reply to the SLO question
    run: dict                  # models.Run of the last forecast
    pending: dict | None       # a Run waiting on the SLO question
    reply: str                 # this turn
    route: str
    question: dict | None      # this turn: {"text", "hitl", "metadata"}
    envelope: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    question: dict | None = None
    envelope: dict | None = None


class ForecastAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, metadata: Mapping[str, Any] | None = None,
                   answer: str | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query) if answer is None else None
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "json_mode": request.parse(query, metadata).json_mode, "answer": answer,
                 "reply": "", "route": "", "question": None, "envelope": None}
        try:
            final = await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"),
                                           TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        return TurnResult(final.get("reply") or "", final.get("question"), final.get("envelope"))


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup_node(state: ForecastState) -> dict:
        if not deps.traceparent:
            return {"reply": prompts.NO_TRACE_CONTEXT}
        return await followup.handle(state, deps)

    async def forecast_node(state: ForecastState) -> dict:
        return await _forecast(state, deps)

    graph = StateGraph(ForecastState)
    graph.add_node("followup", followup_node)
    graph.add_node("forecast", forecast_node)
    graph.add_conditional_edges(START, _entry, ["followup", "forecast"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["forecast", END])
    graph.add_edge("forecast", END)
    return graph


def _entry(state: ForecastState) -> str:
    if state.get("answer") is not None or not state.get("run"):
        return "forecast"
    return "forecast" if intake.has_data(state["query"]) else "followup"


async def _forecast(state: ForecastState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if state.get("answer") is not None:
        return await _answered(state, deps)
    query = state["query"]
    if not intake.has_data(query):
        stored = Run.from_dict(state.get("run"))
        if state.get("route") == "forecast" and stored is not None:
            return await finish(deps, stored, state.get("json_mode", False))
        return {"reply": await _no_data_reply(deps, query)}
    step = await intake.read(deps, query)
    if step.reply:
        return {"reply": step.reply}
    if step.question:
        return {"pending": step.run.to_dict(), "question": step.question}
    return {**await finish(deps, step.run, state.get("json_mode", False)), "pending": None}


async def _answered(state: ForecastState, deps: TurnDeps) -> dict:
    pending = Run.from_dict(state.get("pending"))
    if pending is None:
        return {"reply": "I'm not waiting on an SLO target right now. " + prompts.PASTE_HINT}
    updated = intake.apply_answer(pending, str(state.get("answer") or ""))
    if updated is None:
        return {"reply": prompts.SLO_PICK_FAILED, "question": intake.slo_question(pending)}
    if updated.missing():
        return {"pending": updated.to_dict(), "question": intake.slo_question(updated)}
    return {**await finish(deps, updated, state.get("json_mode", False)), "pending": None}


async def _no_data_reply(deps: TurnDeps, query: str) -> str:
    if request.mentions_domain(query):
        return prompts.NO_DATA
    answer = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                                   {"role": "user", "content": prompts.fence_data(query)}))
    if not answer.strip().upper().startswith(prompts.IN_SCOPE):
        return smalltalk.OFF_TOPIC_REPLY
    return prompts.NO_DATA
