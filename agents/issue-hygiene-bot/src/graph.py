"""The issue-hygiene-bot LangGraph.

    START ─┬─ (a result was given in this context) → followup ─┬─ answered / json / declined → END
           │                                                    ├─ DRAFT:<owners> → draft → END (walk)
           │                                                    └─ NEEDS_PULL ─┐
           └─ (no result yet) ─────────────────────────────────→ check ←───────┘ → END (+ picker)

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId; the graph is
compiled per turn because its nodes close over that turn's dependencies. The picker and the drafts leave
the graph in the turn result; the approve → send walk runs outside it, one card per turn."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import check
import drafting
import envelope
import prompts
import render
import replies
import request as req
import smalltalk
from deps import TurnDeps
from dm import Dm
from envelope import Result
from pick import Pause
from reader import ask_once

__all__ = ["HygieneAgent", "TurnResult"]

_NEW_CHECK = re.compile(r"hygiene|check again|re-?check|refresh|\bteam\s+\w|\bproject\s+\w|stale|90|estimate|"
                        r"no project|open over", re.IGNORECASE)


class HygieneState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    result: dict | None        # envelope.Result of the last check, for follow-ups and the picker
    outcomes: dict[str, str]   # owner key → sent / skipped / denied / blocked / not_sent / not_reached
    draft_keys: list[str]      # this turn: owners named in a follow-up
    dms: list[dict]            # this turn: drafts for the approval walk
    pause: dict | None         # this turn: the picker
    data: dict | None          # this turn: the envelope


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: Pause | None = None
    dms: tuple[Dm, ...] = ()
    data: dict | None = None


class HygieneAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # greeting / small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "draft_keys": [], "dms": [], "pause": None, "data": None}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        pause = final.get("pause")
        return TurnResult(final.get("reply") or "", Pause(**pause) if pause else None,
                          tuple(Dm.from_dict(d) for d in final.get("dms") or ()), final.get("data"))

    async def draft_picked(self, keys: Sequence[str], note: str, context_id: str, deps: TurnDeps) -> TurnResult:
        """Drafts for the owners picked on the multi-select; still nothing is sent."""
        values = await self._values(context_id)
        if not values.get("result"):
            return TurnResult(replies.NO_RESULT_YET)
        owners = _owners(Result.from_state(values["result"]), keys)
        drafted = await drafting.draft(deps, owners, note)
        return TurnResult(drafted.reply, dms=drafted.dms)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each owner's message, so a later turn neither resends nor re-offers it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        values = await self._values(context_id)
        merged = {**dict(values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "dms": [], "pause": None}, as_node="check")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _owners(result: Result, keys: Sequence[str]):
    by_key = {o.key: o for o in result.owners}
    return tuple(by_key[k] for k in keys if k in by_key)


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def followup(state: HygieneState) -> dict:
        return await _followup(deps, state)

    async def run_check(state: HygieneState) -> dict:
        return await _check(deps, state)

    async def draft(state: HygieneState) -> dict:
        owners = _owners(Result.from_state(state["result"]), state.get("draft_keys") or ())
        drafted = await drafting.draft(deps, owners, "")
        return {"reply": drafted.reply, "dms": [d.to_dict() for d in drafted.dms], "route": ""}

    graph = StateGraph(HygieneState)
    for name, node in (("followup", followup), ("check", run_check), ("draft", draft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: "followup" if s.get("result") else "check", ["followup", "check"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["check", "draft", END])
    graph.add_edge("check", END)
    graph.add_edge("draft", END)
    return graph


async def _check(deps: TurnDeps, state: HygieneState) -> dict:
    outcome = await check.run(deps, state["query"], req.parse(state["query"]))
    update = {"reply": outcome.reply, "data": outcome.data, "route": "",
              "pause": {"text": outcome.pause.text, "hitl": outcome.pause.hitl, "metadata": outcome.pause.metadata}
              if outcome.pause else None}
    if outcome.result is not None:  # a refusal or fixed reply keeps the previous result
        update = {**update, "result": outcome.result.to_state(), "outcomes": {}}
    return update


async def _followup(deps: TurnDeps, state: HygieneState) -> dict:
    query = state["query"]
    result = Result.from_state(state["result"])
    outcomes = dict(state.get("outcomes") or {})
    if req.wants_json(query) and not _NEW_CHECK.search(query):  # the stored result as the envelope: no call
        data = envelope.build(result, outcomes, render.summary(result))
        return {"reply": envelope.as_json(data), "data": data, "route": ""}
    if req.asks_for_edit(query):
        return {"reply": replies.EDIT_DECLINED, "route": ""}
    if _NEW_CHECK.search(query) or req.envelope_issue_ids(query):
        return {"route": "check"}
    if not deps.traceparent:
        return {"reply": replies.NO_TRACE_CONTEXT, "route": ""}
    report = render.report(result, outcomes)
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(report)}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "check"}
    if text.startswith(prompts.DRAFT_PREFIX):
        names = {n.strip().casefold() for n in text[len(prompts.DRAFT_PREFIX):].split(",") if n.strip()}
        keys = [o.key for o in result.owners if o.name.casefold() in names and outcomes.get(o.key) != "sent"]
        return {"route": "draft", "draft_keys": keys} if keys else {"reply": replies.NOTHING_PICKED, "route": ""}
    if not text or unbacked(text, report):  # a key, link or number the result does not hold: show the result
        return {"reply": f"{replies.FROM_RESULT_ONLY}\n\n{report}", "route": ""}
    return {"reply": text, "route": ""}


def unbacked(answer: str, source: str) -> bool:
    """Any issue key, link or number in the answer that the stored result does not contain."""
    folded = source.casefold()
    tokens = re.findall(r"https?://[^\s<>()\"']+|\b[A-Za-z][A-Za-z0-9]*-\d+\b|\d+(?:\.\d+)?", answer)
    return any(t.rstrip(".,;").casefold() not in folded for t in tokens)
