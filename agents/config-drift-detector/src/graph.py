"""The config-drift-detector LangGraph.

    START ─┬─ (a report exists, no new paste or file named) → followup → END
           └─ compare → END   (parse → fetch → compare → audit → rank → explain → render; or one question)

Before the graph, in code: a greeting gets the canned intro (zero tool and LLM calls), and a request
without a traceparent gets a fixed reply (zero calls).

State is checkpointed per A2A contextId, so a follow-up is answered from the stored drift records (no
raw tool output is kept). The graph is compiled per turn because its nodes close over that turn's
dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import inputs
import prompts
import smalltalk
import turn
from deps import TurnDeps
from pauses import Pending

__all__ = ["DriftAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("drift.graph")


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pause: Pending | None = None


class DriftState(TypedDict, total=False):
    query: str
    extra: str          # an answer to "which IaC file?", added to the original message's inputs
    wants_json: bool
    reply: str
    data: dict
    ask_path: bool
    report: dict        # the last report in this context: what follow-ups are answered from


class DriftAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, wants_json: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return TurnResult(canned)
        return await self._run(query, "", context_id, deps, wants_json)

    async def resume(self, pending: Pending, answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(pending.query, answer, context_id, deps, pending.wants_json)

    async def _run(self, query: str, extra: str, context_id: str, deps: TurnDeps, wants_json: bool) -> TurnResult:
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        start = {"query": query, "extra": extra, "wants_json": wants_json, "reply": "", "data": {}, "ask_path": False}
        final = await graph.ainvoke(start, {"configurable": {"thread_id": context_id}}, durability="exit")
        if final.get("ask_path"):
            return TurnResult(final.get("reply") or prompts.ASK_PATH, None, Pending(query, wants_json))
        return TurnResult(final.get("reply") or prompts.NOTHING_TO_COMPARE, final.get("data") or None)


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def compare_node(state: DriftState) -> dict:
        return await _compare(state, deps)

    async def followup_node(state: DriftState) -> dict:
        return await _followup(state, deps)

    def first(state: DriftState) -> str:
        inp = inputs.parse(state["query"], state.get("extra") or "")
        fresh = inp.has_paste or inp.path or inp.too_long or state.get("extra")
        return "followup" if state.get("report") and not fresh else "compare"

    graph = StateGraph(DriftState)
    graph.add_node("compare", compare_node)
    graph.add_node("followup", followup_node)
    graph.add_conditional_edges(START, first, ["compare", "followup"])
    graph.add_edge("compare", END)
    graph.add_edge("followup", END)
    return graph


def _wants_json(state: DriftState, inp: inputs.Inputs) -> bool:
    return bool(state.get("wants_json") or inp.wants_json)


async def _compare(state: DriftState, deps: TurnDeps) -> dict:
    inp = inputs.parse(state["query"], state.get("extra") or "")
    if not (inp.has_paste or inp.path or inp.too_long):
        verdict = await followup.classify(deps, state["query"])
        if verdict == prompts.OFF_TOPIC:
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
        return {"reply": prompts.NO_REPORT if isinstance(verdict, followup.Ask) else prompts.NOTHING_TO_COMPARE}
    outcome = await turn.run(deps, inp, _wants_json(state, inp))
    update = {"reply": outcome.reply, "data": outcome.data or {}, "ask_path": outcome.ask_path}
    return {**update, "report": outcome.report} if outcome.report else update


async def _followup(state: DriftState, deps: TurnDeps) -> dict:
    inp = inputs.parse(state["query"])
    ask = followup.match(inp.prose or state["query"])
    if ask is None and inp.audit_text:
        ask = followup.Ask("who_changed")
    if ask is None:
        verdict = await followup.classify(deps, state["query"])
        if verdict == prompts.OFF_TOPIC:
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
        if not isinstance(verdict, followup.Ask):
            return {"reply": prompts.NOTHING_TO_COMPARE}
        ask = verdict
    outcome = followup.answer(ask, state["report"], inp, deps, _wants_json(state, inp))
    update = {"reply": outcome.reply, "data": outcome.data or {}}
    return {**update, "report": outcome.report} if outcome.report else update
