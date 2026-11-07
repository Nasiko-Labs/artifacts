"""The churn-warning LangGraph.

    START ─┬─ (a list was given in this context) → followup ─┬─ answered ────────────→ END
           │                                                 ├─ only above N → filter → END
           │                                                 ├─ DRAFT:<ids> → draft → END (walk)
           │                                                 └─ NEEDS_SCAN ─┐
           └─ (no list yet, or pasted rows) ────────────────────→ scan ←────┘
         scan: pasted rows → rank (code) → END
               discover → subscriptions → changes → flag (code) → mail → desk (optional) → score (code) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the list. The graph is compiled
per turn because its nodes close over that turn's dependencies (LLM client, gateway, inbound
traceparent); the checkpointer is shared. Drafts and the picker leave the graph in the turn result; the
approve → send walk runs outside it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import drafting
import mrr
import pasted
import picker
import prompts
import render
import scan
import smalltalk
from deps import TurnDeps
from drafts import Draft
from reader import ask_once
from request import Request, parse_request
from scoring import from_state, to_state

__all__ = ["ChurnAgent", "TurnDeps", "TurnResult"]

_RESCAN = ("again", "refresh", "rescan", "re-scan", "re-run", "rerun", "recheck", "re-check")


class ChurnState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    items: list[dict]          # the rows last shown, numbered: what "#2" refers to
    outcomes: dict[str, str]   # account id → sent / skipped / denied / blocked / not_sent
    meta: dict                 # render.ListMeta of the list last shown
    draft_ids: list[str]
    typed_email: str
    drafts: list[dict]         # this turn: drafts ready for the approval walk
    pick: dict | None          # this turn: a question to pause on
    envelope: dict | None      # this turn: the envelope of a list shown


@dataclass(frozen=True)
class TurnResult:
    reply: str
    drafts: tuple[Draft, ...] = ()
    pick: picker.Pick | None = None
    envelope: dict | None = None


class ChurnAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "draft_ids": [], "typed_email": "", "drafts": [],
                 "pick": None, "envelope": None}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return _result(final)

    async def draft_picked(self, keys: Sequence[str], instruction: str, context_id: str, deps: TurnDeps,
                           typed_email: str = "") -> TurnResult:
        """Drafts for the accounts picked on the multi-select (by account id), with any typed instruction."""
        values = await self._values(context_id)
        rows = from_state(values.get("items"))
        ids = [r.id for k in keys for r in rows if r.key == k]
        turn = await drafting.draft_turn(deps, instruction, ids, rows, render.ListMeta.from_dict(values.get("meta")),
                                         dict(values.get("outcomes") or {}), typed_email)
        return TurnResult(turn.reply, turn.drafts, turn.ask_email)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each draft, so later turns neither resend nor redraft it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        values = await self._values(context_id)
        merged = {**dict(values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "drafts": []}, as_node="scan")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _result(final: Mapping[str, Any]) -> TurnResult:
    drafts = tuple(Draft.from_dict(d) for d in final.get("drafts") or ())
    pick = final.get("pick")
    return TurnResult(final.get("reply") or "", drafts, picker.Pick(**pick) if pick else None,
                      final.get("envelope") or None)


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def followup(state: ChurnState) -> dict:
        return await _followup(state, deps)

    async def scan_node(state: ChurnState) -> dict:
        return await _scan(state, deps)

    async def filter_node(state: ChurnState) -> dict:
        return _filter(state)

    async def draft(state: ChurnState) -> dict:
        turn = await drafting.draft_turn(deps, state["query"], state.get("draft_ids") or (),
                                         from_state(state.get("items")), render.ListMeta.from_dict(state.get("meta")),
                                         dict(state.get("outcomes") or {}), state.get("typed_email") or "")
        pick = turn.ask_email
        return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "route": "",
                "pick": _pick_state(pick)}

    graph = StateGraph(ChurnState)
    for name, node in (("followup", followup), ("scan", scan_node), ("filter", filter_node), ("draft", draft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "scan"])
    graph.add_conditional_edges("followup", _route, ["scan", "draft", "filter", END])
    for terminal in ("scan", "filter", "draft"):
        graph.add_edge(terminal, END)
    return graph


def _entry(state: ChurnState) -> str:
    if pasted.find_rows(state.get("query") or ""):
        return "scan"
    return "followup" if state.get("items") else "scan"


def _route(state: ChurnState) -> str:
    return state.get("route") or END


async def _followup(state: ChurnState, deps: TurnDeps) -> dict:
    query = state["query"]
    req = parse_request(query)
    if req.draft_ids:  # "draft outreach for #1 and #3", parsed in code: no LLM call to route it
        return {"route": "draft", "draft_ids": list(req.draft_ids), "typed_email": req.email}
    if req.min_mrr is not None and not req.explicit_window and not _asks_rescan(query):
        return {"route": "filter"}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    rows, meta = from_state(state.get("items")), render.ListMeta.from_dict(state.get("meta"))
    listing = render.render_for_model(rows, meta, dict(state.get("outcomes") or {}))
    # Only the fenced list, never earlier replies: those carry tool text outside a fence.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_SCAN):
        return {"route": "scan"}
    if text.startswith(prompts.DRAFT_PREFIX):
        return {"route": "draft", "draft_ids": list(drafting.requested_ids(text)), "typed_email": req.email}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


def _asks_rescan(query: str) -> bool:
    lowered = query.casefold()
    return any(word in lowered for word in _RESCAN)


def _filter(state: ChurnState) -> dict:
    """"Only above 2,000": the stored list re-shown with fewer rows (same numbers), no scan, no LLM call."""
    req = parse_request(state["query"])
    rows = from_state(state.get("items"))
    kept = [r for r in rows if mrr.to_major(r.account.mrr_minor, r.account.currency) >= (req.min_mrr or 0)]
    meta = render.ListMeta.from_dict(state.get("meta"))
    shown = replace(meta, below_min=meta.below_min + len(rows) - len(kept), min_mrr=f"{req.min_mrr:,}")
    outcomes = dict(state.get("outcomes") or {})
    return {"reply": render.render_list(kept, shown, outcomes), "route": "",
            "envelope": render.envelope(kept, shown)}


async def _scan(state: ChurnState, deps: TurnDeps) -> dict:
    req: Request = parse_request(state["query"])
    outcome = await scan.run(deps, state["query"], req)
    if outcome.reply is not None:  # a fixed reply keeps the previous list
        return {"reply": outcome.reply, "route": ""}
    rows, meta = outcome.rows, outcome.meta
    pick = picker.question(rows, {})
    return {"reply": render.render_list(rows, meta, {}), "items": to_state(rows), "meta": meta.to_dict(),
            "outcomes": {}, "route": "", "envelope": render.envelope(rows, meta), "pick": _pick_state(pick)}


def _pick_state(pick: picker.Pick | None) -> dict | None:
    return {"text": pick.text, "hitl": pick.hitl, "metadata": pick.metadata} if pick else None
