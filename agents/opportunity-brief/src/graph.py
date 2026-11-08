"""The opportunity-brief LangGraph.

    START ─┬─ (a brief is stored and the message is a follow-up command) → followup → END
           └─ otherwise → intake ─┬─ off-topic / no trace → fixed reply → END
                                  ├─ no topic → pause (Q1) → END
                                  ├─ refine → write → verify → END      (stored items, no fetch)
                                  └─ ok → collect → cluster → write → verify → score → render → END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId, so follow-ups
see the last brief's items, themes and envelope. The graph is compiled per turn because its nodes close
over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import intake
import smalltalk
from brief import Outcome
from deps import TurnDeps
from hitl import Pending


class BriefState(TypedDict, total=False):
    query: str
    wants_json: bool
    reply: str
    envelope: dict | None   # this turn's
    pause: dict | None      # this turn's
    brief: dict             # the last full brief: spec, items, clusters, run, envelope


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    pause: Pending | None = None


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _apply(outcome: Outcome, pause: Pending | None = None) -> dict:
    update: dict[str, Any] = {"reply": outcome.reply, "envelope": outcome.envelope,
                              "pause": pause.to_metadata() if pause else None}
    if outcome.state:
        update["brief"] = outcome.state
    return update


class BriefAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, text: str, wants_json: bool, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(text)
        if canned is not None:  # greeting or small talk: no LLM, HN or tool call; state untouched
            return TurnResult(canned)
        graph = _build(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": text, "wants_json": wants_json, "reply": "", "envelope": None, "pause": None}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return _result(final)

    async def resume(self, pending: Pending, topic: str, context_id: str, deps: TurnDeps) -> TurnResult:
        outcome, pause = await intake.with_topic(deps, pending, topic)
        await self._record(context_id, _apply(outcome, pause))
        return TurnResult(outcome.reply, outcome.envelope, pause)

    async def _record(self, context_id: str, update: dict) -> None:
        graph = _build(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), update, as_node="intake")


def _result(final: dict) -> TurnResult:
    pause = Pending.from_metadata(final.get("pause"))
    return TurnResult(final.get("reply") or "", final.get("envelope"), pause)


def _build(deps: TurnDeps | None) -> StateGraph:
    async def route_followup(state: BriefState) -> dict:
        command = followup.detect(state["query"])
        outcome = await followup.handle(deps, command, state.get("brief") or {}, state.get("wants_json", False))
        return _apply(outcome)

    async def route_intake(state: BriefState) -> dict:
        outcome, pause = await intake.new_request(deps, state["query"], state.get("wants_json", False),
                                                  state.get("brief") or {})
        return _apply(outcome, pause)

    graph = StateGraph(BriefState)
    graph.add_node("followup", route_followup)
    graph.add_node("intake", route_intake)
    graph.add_conditional_edges(START, _entry, ["followup", "intake"])
    graph.add_edge("followup", END)
    graph.add_edge("intake", END)
    return graph


def _entry(state: BriefState) -> str:
    command = followup.detect(state["query"])
    if command is None:
        return "intake"
    has_brief = bool((state.get("brief") or {}).get("envelope"))
    return "followup" if has_brief or command.kind != "risks" else "intake"
