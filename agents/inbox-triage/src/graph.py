"""The inbox-triage LangGraph.

    START ─┬─ (a list was shown in this context) → followup ─┬─ answered in code or by one model call → END
           │                                                 └─ RETRIAGE ─┐
           └─ (no list yet) ─────────────────────────────────→ triage ←───┘ → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the list, the threads it was built
from (for a VIP re-run), the older slice not yet shown ("continue") and the VIPs named in this conversation.
The graph is compiled per turn because its nodes close over that turn's dependencies; the checkpointer is
shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import coverage
import fetch
import followup
import parse
import paste
import prompts
import render
import smalltalk
import triage
from classify import ask_once
from deps import TurnDeps
from discovery import discover
from signals import is_vip
from view import reply_only, shown

__all__ = ["TriageAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("inbox_triage.graph")

_UNREADABLE_MIN_CHARS = 200


class TriageState(TypedDict, total=False):
    query: str
    wants_json: bool
    reply: str
    envelope: dict | None
    route: str
    threads: list[dict]        # the list last shown, in display order: what "#3" refers to
    mails: dict[str, dict]     # the threads as read, by id (for a VIP re-run)
    backlog: list[dict]        # older threads beyond the cap, for "continue"
    meta: dict
    checks: dict
    vips: list[str]
    me: list[str]


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


class TriageAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, wants_json: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "wants_json": wants_json, "reply": "", "envelope": None, "route": ""}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or prompts.UNREADABLE_RESULTS, final.get("envelope"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup_node(state: TriageState) -> dict:
        return await followup.handle(state, deps)

    async def triage_node(state: TriageState) -> dict:
        return await _triage(state, deps)

    graph = StateGraph(TriageState)
    graph.add_node("followup", followup_node)
    graph.add_node("triage", triage_node)
    graph.add_conditional_edges(START, lambda s: "followup" if s.get("threads") else "triage", ["followup", "triage"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["triage", END])
    graph.add_edge("triage", END)
    return graph


async def _triage(state: TriageState, deps: TurnDeps) -> dict:
    query = state["query"]
    request = parse.parse_request(query, deps.today, bool(state.get("wants_json")))
    if not deps.traceparent:
        return reply_only(prompts.NO_TRACE_CONTEXT)
    if followup.is_write_request(query):
        return reply_only(prompts.READ_ONLY_REPLY)
    vips = list(dict.fromkeys([*(state.get("vips") or ()), *request.vips]))
    if paste.is_paste(query):
        return await _pasted(query, request, vips, deps)
    if not followup.looks_like_triage(query, request):
        verdict = await ask_once(deps, ({"role": "system", "content": prompts.INTENT_SYSTEM},
                                        {"role": "user", "content": query}))
        if verdict.startswith(prompts.READ_ONLY):
            return reply_only(prompts.READ_ONLY_REPLY)
        if not verdict.startswith(prompts.TRIAGE):
            return reply_only(smalltalk.OFF_TOPIC_REPLY)
    return await _from_inbox(request, vips, deps)


async def _from_inbox(request: parse.Request, vips: list[str], deps: TurnDeps) -> dict:
    catalog, failure = await discover(deps)
    if failure is not None:
        return reply_only(failure)
    reader = fetch.Reader.build(deps, catalog)
    listing = await fetch.read_inbox(deps, reader, parse.search_query(request, deps.today))
    status = coverage.assess(catalog.tools, reader.health.dropped)
    if status.state != "ok":
        return reply_only(coverage.unavailable_reply(status))
    if listing.reply is not None:
        return reply_only(listing.reply)
    mails = [m for m in listing.mails if not request.sender or is_vip(m.sender, [request.sender])]
    if not mails:
        unreadable = any(len(r.text) >= _UNREADABLE_MIN_CHARS for r in reader.fenced.records)
        label = request.window_label or f"last {request.window_days} days"
        return {**reply_only(prompts.UNREADABLE_RESULTS if unreadable else prompts.nothing_found(label)), "vips": vips}
    notes = [] if listing.me else [prompts.NO_IDENTITY_NOTE]
    head, backlog = mails[:triage.THREAD_CAP], mails[triage.THREAD_CAP:]
    outcome = await triage.run(deps, head, vips, listing.me, reader=reader, notes=notes, capped=bool(backlog))
    meta = render.Meta(label=request.window_label, total=len(mails), already_answered=outcome.already_answered)
    return {**shown(outcome.threads, meta, outcome.checks, request.wants_json), "vips": vips, "me": list(listing.me),
            "mails": {k: m.to_dict() for k, m in outcome.mails.items()}, "backlog": [m.to_dict() for m in backlog]}


async def _pasted(query: str, request: parse.Request, vips: list[str], deps: TurnDeps) -> dict:
    mails = paste.split(query)
    extra = paste.count(query) - len(mails)
    notes = [f"Only the first {len(mails)} pasted threads were triaged; {extra} more were not read."] if extra else []
    outcome = await triage.run(deps, mails, vips, (), notes=notes, pasted=True)
    meta = render.Meta(label="", total=len(mails), already_answered=outcome.already_answered, pasted=True)
    return {**shown(outcome.threads, meta, outcome.checks, request.wants_json), "vips": vips, "me": [],
            "mails": {k: m.to_dict() for k, m in outcome.mails.items()}, "backlog": []}

