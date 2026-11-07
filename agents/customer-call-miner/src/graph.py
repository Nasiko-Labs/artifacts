"""The customer-call-miner LangGraph.

    START ─┬─ (results exist in this context) → followup ─┬─ answered / declined ────────────→ END
           │                                              ├─ CONTINUE ──────────→ transcripts
           │                                              ├─ NOTES / DIGEST / NOTE:x → offer → END
           │                                              └─ NEEDS_PULL ─┐
           └─ (no results yet) ──────────────────────────────────────────→ calls
      calls ─┬─ fixed reply → END
             └─ match → transcripts → extract → cluster → render → offer → END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId, so a follow-up
sees the stored results. The graph is compiled per turn because its nodes close over that turn's
dependencies; the checkpointer is shared. Writes leave the graph as a walk (walk.py) on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import mining
import offer
import prompts
import smalltalk
from deps import TurnDeps
from reader import ask_once
from walk import Walk

log = logging.getLogger("call_miner.graph")


class MinerState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    offer_mode: str
    single: str
    window: dict
    calls: list[dict]            # external calls in the period, in processing order
    internal_only: int
    accounts: list[dict]
    matches: dict[str, str]      # call id → company id ("" when unmatched)
    arr_available: bool
    crm_line: str
    batch: list[str]             # this turn's call ids
    processed: list[str]
    problems: dict[str, str]
    partly: dict[str, list[int]]
    items: list[dict]
    dropped: dict[str, int]
    groupings: dict[str, dict[str, str]]
    report: str                  # the last report: what follow-ups answer from
    written: dict[str, str]      # company id / "digest" → sent / skipped / denied / blocked / not_sent
    channel: str
    walk: dict                   # this turn: the write walk to start


@dataclass(frozen=True)
class TurnResult:
    reply: str
    walk: Walk = field(default_factory=Walk)


class MinerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(mining.Scope(deps)).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "offer_mode": "all", "single": "", "batch": [], "walk": {}}
        channel = offer.channel_from(query)
        if channel:
            fresh["channel"] = channel
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        stored = final.get("walk") or {}
        return TurnResult(final.get("reply") or "", Walk.from_metadata(stored) if stored else Walk())

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what was written, so later turns do not offer it again."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("written") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"written": merged, "walk": {}}, as_node="offer")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(scope: mining.Scope | None) -> StateGraph:
    async def followup(state: MinerState) -> dict:
        return await _followup(state, scope.deps)

    async def calls(state: MinerState) -> dict:
        return await mining.calls_node(state, scope)

    async def match(state: MinerState) -> dict:
        return await mining.match_node(state, scope)

    async def transcripts(state: MinerState) -> dict:
        return await mining.transcripts_node(state, scope)

    async def extract(state: MinerState) -> dict:
        return await mining.extract_node(state, scope)

    async def cluster(state: MinerState) -> dict:
        return await mining.cluster_node(state, scope)

    async def render(state: MinerState) -> dict:
        return mining.render_node(state, scope)

    async def offer_node(state: MinerState) -> dict:
        return await _offer(state, scope.deps)

    graph = StateGraph(MinerState)
    for name, node in (("followup", followup), ("calls", calls), ("match", match), ("transcripts", transcripts),
                       ("extract", extract), ("cluster", cluster), ("render", render), ("offer", offer_node)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "calls"])
    graph.add_conditional_edges("followup", _route, ["calls", "transcripts", "offer", END])
    graph.add_conditional_edges("calls", _route, ["match", END])
    graph.add_edge("match", "transcripts")
    graph.add_conditional_edges("transcripts", _route, ["extract", END])
    graph.add_edge("extract", "cluster")
    graph.add_edge("cluster", "render")
    graph.add_edge("render", "offer")
    graph.add_edge("offer", END)
    return graph


def _entry(state: MinerState) -> str:
    return "followup" if state.get("report") else "calls"


def _route(state: MinerState) -> str:
    return state.get("route") or END


async def _followup(state: MinerState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state.get('report') or '')}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    word = text.strip().strip(".").strip()
    if word.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if word.startswith(prompts.NEEDS_PULL):
        return {"route": "calls"}
    if word.startswith(prompts.CONTINUE):
        if not mining.next_batch(state, deps.config.max_calls):
            return {"reply": prompts.NOTHING_LEFT, "route": ""}
        return {"route": "transcripts"}
    if word.startswith(prompts.NOTE_PREFIX):
        return {"route": "offer", "offer_mode": "single", "single": word[len(prompts.NOTE_PREFIX):].strip()}
    if word.startswith(prompts.NOTES):
        return {"route": "offer", "offer_mode": "notes"}
    if word.startswith(prompts.DIGEST):
        return {"route": "offer", "offer_mode": "digest"}
    return {"reply": text or prompts.NO_PRIOR_RESULTS, "route": ""}


async def _offer(state: MinerState, deps: TurnDeps) -> dict:
    mode = state.get("offer_mode") or "all"
    walk, lines = await offer.plan(state, deps, mode, state.get("single") or "")
    base = (state.get("reply") or "") if mode == "all" else ""
    reply = "\n\n".join(part for part in (base, lines) if part) or "There is nothing to write from these results."
    has_work = bool(walk.notes or walk.digest)
    stored = replace(walk, preamble=reply).to_metadata() if has_work else {}
    return {"reply": reply, "walk": stored, "route": ""}
