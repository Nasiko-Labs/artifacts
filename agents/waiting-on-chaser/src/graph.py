"""The waiting-on LangGraph.

    START ─┬─ (a list was given in this context) → followup ─┬─ answered ───────────→ END
           │                                                 ├─ NUDGE:<ids> → draft → END (walk)
           │                                                 ├─ SET_MANAGER → rescore → END
           │                                                 └─ NEEDS_SWEEP ─┐
           └─ (no list yet) ─────────────────────────────────→ sweep ←───────┘
                          sweep ─┬─ fixed reply → END
                                 ├─ asks → replies → score (code) → END
                                 └─ asks, out of time → score (replies not checked) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the list, the user's identity
and the manager they named. The graph is compiled per turn because its nodes close over that turn's
dependencies (LLM client, gateway, inbound traceparent); the checkpointer is shared. Drafts and the
picker leave the graph in the turn result; the approve → send walk runs outside it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import asks as a
import drafting
import picker
import prompts
import render
import replies as replies_step
import scoring
import smalltalk
import sweep as sweep_step
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from drafts import Draft
from fence import FencedGateway
from tool_health import ToolHealth

__all__ = ["ChaserAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("waiting_on.graph")

_MAX_MANAGER_CHARS = 120


class ChaserState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    items: list[dict]          # the list last shown, numbered: what "#2" refers to
    outcomes: dict[str, str]   # ask key → sent / skipped / denied / blocked / not_sent
    meta: dict                 # render.ListMeta of the list last shown
    me: list[str]              # the user's own address / chat id, as a tool returned it
    manager: str | None        # named by the user in this conversation
    new_manager: str           # this turn: a manager parsed from the message in code
    checked: list[dict]        # this turn: post-checked sweep asks
    judged: list[dict]         # this turn: asks after the reply check
    sweep_meta: dict           # this turn: window, drop/fresh/cap counts, notes
    nudge_ids: list[str]
    drafts: list[dict]         # this turn: drafts ready for the approval walk
    pick: dict | None          # this turn: the "which should I nudge?" question


@dataclass(frozen=True)
class TurnResult:
    reply: str
    drafts: tuple[Draft, ...] = ()
    pick: picker.Pick | None = None


class _Scope:
    """One turn's read side, shared by sweep and the reply check (never persisted)."""

    def __init__(self, deps: TurnDeps):
        self.deps = deps
        self.reader: sweep_step.Reader | None = None


class ChaserAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(_Scope(deps)).compile(checkpointer=self._checkpointer)
        manager = a.manager_from(query)
        fresh = {"query": query, "reply": "", "route": "", "checked": [], "judged": [], "nudge_ids": [],
                 "drafts": [], "pick": None, "sweep_meta": {}, "new_manager": manager or ""}
        if manager:
            fresh = {**fresh, "manager": manager}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        drafts = tuple(Draft.from_dict(d) for d in final.get("drafts") or ())
        pick = final.get("pick")
        return TurnResult(final.get("reply") or "", drafts, picker.Pick(**pick) if pick else None)

    async def draft_picked(self, keys: Sequence[str], instruction: str, context_id: str,
                           deps: TurnDeps) -> TurnResult:
        """Drafts for the asks picked on the multi-select (by stable key), with any typed instruction."""
        values = await self._values(context_id)
        items = a.from_state(values.get("items"))
        ids = [x.id for k in keys for x in items if x.key == k]
        turn = await drafting.draft_turn(deps, instruction, ids, items, _view(values, deps))
        return TurnResult(turn.reply, turn.drafts)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each nudge, so later turns neither resend nor redraft it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        values = await self._values(context_id)
        merged = {**dict(values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "drafts": []}, as_node="score")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _view(state: Mapping[str, Any], deps: TurnDeps) -> render.View:
    return render.View(now=deps.now, me=tuple(state.get("me") or ()), manager=state.get("manager") or None,
                       outcomes=dict(state.get("outcomes") or {}))


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def followup(state: ChaserState) -> dict:
        return await _followup(state, scope.deps)

    async def sweep(state: ChaserState) -> dict:
        return await _sweep(state, scope)

    async def replies(state: ChaserState) -> dict:
        judged = await replies_step.check_replies(scope.deps, scope.reader, a.from_state(state["checked"]))
        return {"judged": a.to_state(judged), "route": "score"}

    async def score(state: ChaserState) -> dict:
        return _score(state, scope.deps)

    async def rescore(state: ChaserState) -> dict:
        return _rescore(state, scope.deps)

    async def draft(state: ChaserState) -> dict:
        turn = await drafting.draft_turn(scope.deps, state["query"], state.get("nudge_ids") or (),
                                         a.from_state(state.get("items")), _view(state, scope.deps))
        return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "route": ""}

    graph = StateGraph(ChaserState)
    for name, node in (("followup", followup), ("sweep", sweep), ("replies", replies), ("score", score),
                       ("rescore", rescore), ("draft", draft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "sweep"])
    graph.add_conditional_edges("followup", _route, ["sweep", "draft", "rescore", END])
    graph.add_conditional_edges("sweep", _route, ["replies", "score", END])
    graph.add_edge("replies", "score")
    for terminal in ("score", "rescore", "draft"):
        graph.add_edge(terminal, END)
    return graph


def _entry(state: ChaserState) -> str:
    return "followup" if state.get("items") else "sweep"


def _route(state: ChaserState) -> str:
    return state.get("route") or END


async def _followup(state: ChaserState, deps: TurnDeps) -> dict:
    if state.get("new_manager"):  # "my manager is X", parsed in code: re-rank, no LLM call
        return {"route": "rescore"}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    listing = render.render_for_model(a.from_state(state.get("items")), _view(state, deps))
    # Only the fenced list, never earlier replies: those carry message text outside a fence.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    text = await sweep_step.ask_once(deps, ({"role": "system", "content": system},
                                            {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_SWEEP):
        return {"route": "sweep"}
    if text.startswith(prompts.NUDGE_PREFIX):
        return {"route": "draft", "nudge_ids": list(drafting.requested_ids(text))}
    if text.startswith(prompts.SET_MANAGER_PREFIX):
        who = _first_line(text[len(prompts.SET_MANAGER_PREFIX):])[:_MAX_MANAGER_CHARS]
        return {"route": "rescore", "manager": who} if who else {"reply": prompts.UNREADABLE_ANSWER, "route": ""}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""


async def _sweep(state: ChaserState, scope: _Scope) -> dict:
    request = a.parse_request(state["query"])
    catalog, failure = await discover(scope.deps, "read")
    if failure is not None:
        return {"reply": failure, "route": ""}
    scope.reader = _reader(scope.deps, catalog)
    outcome = await sweep_step.sweep(scope.deps, scope.reader, state["query"], request)
    if outcome.reply is not None:  # a refusal or an empty sweep keeps the previous list
        return {"reply": outcome.reply, "route": ""}
    checked = outcome.checked
    notes = list(outcome.notes)
    route = "replies"
    if scope.deps.elapsed() > replies_step.TIME_BUDGET_SECS:
        log.info("sweep ended after %.0fs; skipping the reply check", scope.deps.elapsed())
        notes, route = [*notes, prompts.REPLIES_SKIPPED_NOTE], "score"
    meta = {"window_days": request.window_days, "dropped": checked.dropped, "too_fresh": checked.too_fresh,
            "capped": checked.capped, "notes": notes}
    asks_now = checked.asks if route == "replies" else replies_step.unchecked(checked.asks)
    return {"checked": a.to_state(checked.asks), "judged": a.to_state(asks_now), "me": list(outcome.me),
            "sweep_meta": meta, "route": route}


def _reader(deps: TurnDeps, catalog: Catalog) -> sweep_step.Reader:
    tools = catalog.read_tools()
    log.info("read tools offered: %s", [t.name for t in tools])
    fenced = FencedGateway(deps.gateway)
    return sweep_step.Reader(tools=tools, fenced=fenced, health=ToolHealth(fenced), source_of=catalog.source_of,
                             helpers=frozenset({RECOVER_META}))


def _score(state: ChaserState, deps: TurnDeps) -> dict:
    view = render.View(now=deps.now, me=tuple(state.get("me") or ()), manager=state.get("manager") or None,
                       outcomes={})
    ranked = scoring.rank(a.from_state(state.get("judged")), deps.now, view.me, view.manager)
    sweep_meta = dict(state.get("sweep_meta") or {})
    meta = render.ListMeta.from_dict({**sweep_meta, "capped": int(sweep_meta.get("capped") or 0) + ranked.capped,
                                      "answered": ranked.answered}, a.DEFAULT_WINDOW_DAYS)
    return {**_shown(ranked.items, meta, view), "outcomes": {}, "meta": meta.to_dict()}


def _rescore(state: ChaserState, deps: TurnDeps) -> dict:
    view = _view(state, deps)
    ranked = scoring.rank(a.from_state(state.get("items")), deps.now, view.me, view.manager)
    meta = render.ListMeta.from_dict(state.get("meta"), a.DEFAULT_WINDOW_DAYS)
    shown = _shown(ranked.items, meta, view)
    lead = f"Got it: I'll treat {view.manager} as your manager and rank asks to them higher."
    return {**shown, "reply": f"{lead}\n\n{shown['reply']}"}


def _shown(items: Sequence[a.Ask], meta: render.ListMeta, view: render.View) -> dict:
    pick = picker.question(items, view)
    return {"reply": render.render_list(items, meta, view), "items": a.to_state(items), "route": "",
            "pick": {"text": pick.text, "hitl": pick.hitl, "metadata": pick.metadata} if pick else None}
