"""The promise-tracker LangGraph.

    START ─┬─ (a list was given in this context) → followup ─┬─ answered ──────────→ END
           │                                                 ├─ DRAFT:<ids> → draft → END
           │                                                 └─ NEEDS_SWEEP ─┐
           └─ (no list yet) ─────────────────────────────────→ sweep ←───────┘
                                     sweep ─┬─ fixed reply → END
                                            └─ candidates → verify → render → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id), so a follow-up sees the list from an earlier
turn. The graph is compiled per turn because its nodes close over that turn's dependencies (LLM
client, gateway, inbound traceparent); the checkpointer is shared. Drafts leave the graph in the
turn result; the approve → send walk runs outside it, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import checks
import drafting
import promises as p
import prompts
import render
import smalltalk
import sweep as sweep_step
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from drafts import Draft
from fence import FencedGateway
from tool_health import ToolHealth

__all__ = ["PromiseAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("promise_tracker.graph")


class TrackerState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    window_days: int
    dropped: int
    items: list[dict]          # the list last shown, numbered: what "#2" refers to
    outcomes: dict[str, str]   # promise number → sent / skipped / denied / blocked / not_sent
    candidates: list[dict]     # this turn: checked sweep candidates, for verify
    judged: list[dict]         # this turn: candidates after verify
    draft_ids: list[str]
    notes: list[str]           # this turn: sources that could not be searched, step limit
    drafts: list[dict]         # this turn: drafts ready for the approval walk


@dataclass(frozen=True)
class TurnResult:
    reply: str
    drafts: tuple[Draft, ...] = ()


class _Scope:
    """One turn's read side (tools, tool-output record, failures), shared by sweep and verify (never persisted)."""

    def __init__(self, deps: TurnDeps):
        self.deps = deps
        self.reader: sweep_step.Reader | None = None


class PromiseAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(_Scope(deps)).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "candidates": [], "judged": [], "draft_ids": [],
                 "drafts": [], "notes": []}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        drafts = tuple(Draft.from_dict(d) for d in final.get("drafts") or ())
        return TurnResult(final.get("reply") or "", drafts)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        """Remember what happened to each draft, so later turns neither resend nor redraft it."""
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "drafts": []}, as_node="render")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def followup(state: TrackerState) -> dict:
        return await _followup(state, scope.deps)

    async def sweep(state: TrackerState) -> dict:
        return await _sweep(state, scope)

    async def verify(state: TrackerState) -> dict:
        judged = await sweep_step.verify(scope.deps, scope.reader, p.from_state(state["candidates"]))
        return {"judged": p.to_state(judged)}

    async def render_list(state: TrackerState) -> dict:
        return _render(state, scope.deps.today)

    async def draft(state: TrackerState) -> dict:
        return await _draft(state, scope.deps)

    graph = StateGraph(TrackerState)
    for name, node in (("followup", followup), ("sweep", sweep), ("verify", verify), ("render", render_list),
                       ("draft", draft)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "sweep"])
    graph.add_conditional_edges("followup", _route, ["sweep", "draft", END])
    graph.add_conditional_edges("sweep", _route, ["verify", END])
    graph.add_edge("verify", "render")
    graph.add_edge("render", END)
    graph.add_edge("draft", END)
    return graph


def _entry(state: TrackerState) -> str:
    return "followup" if state.get("items") else "sweep"


def _route(state: TrackerState) -> str:
    return state.get("route") or END


async def _followup(state: TrackerState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    items = p.from_state(state.get("items"))
    listing = render.render_for_followup(items, state.get("outcomes") or {}, deps.today)
    # Only the fenced list, never earlier replies: those carry message text outside a fence.
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    text = await sweep_step.ask_once(deps, ({"role": "system", "content": system},
                                            {"role": "user", "content": state["query"]}))
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.NEEDS_SWEEP):
        return {"route": "sweep"}
    if text.startswith(prompts.DRAFT_PREFIX):
        return {"route": "draft", "draft_ids": list(drafting.requested_ids(text))}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


async def _sweep(state: TrackerState, scope: _Scope) -> dict:
    window = p.window_days(state["query"])
    catalog, failure = await discover(scope.deps, "read")
    if failure is not None:
        return {"reply": failure, "route": ""}
    scope.reader = _reader(scope.deps, catalog)
    outcome = await sweep_step.sweep(scope.deps, scope.reader, state["query"], window)
    if outcome.reply is not None:  # a refusal or an empty sweep keeps the previous list
        return {"reply": outcome.reply, "route": ""}
    return {"candidates": p.to_state(outcome.candidates), "dropped": outcome.dropped, "window_days": window,
            "notes": list(outcome.notes), "route": "verify"}


def _reader(deps: TurnDeps, catalog: Catalog) -> sweep_step.Reader:
    tools = catalog.read_tools()
    log.info("read tools offered: %s", [t.name for t in tools])
    fenced = FencedGateway(deps.gateway)
    return sweep_step.Reader(tools=tools, fenced=fenced, health=ToolHealth(fenced), source_of=catalog.source_of,
                             helpers=frozenset({RECOVER_META}))


def _render(state: TrackerState, today: date) -> dict:
    ledger = checks.ledger(p.from_state(state.get("judged")), today)
    window = state.get("window_days") or p.DEFAULT_WINDOW_DAYS
    reply = render.render_ledger(ledger, today=today, window_days=window, dropped=state.get("dropped") or 0,
                                 notes=state.get("notes") or ())
    return {"reply": reply, "items": p.to_state(ledger.items), "outcomes": {}, "route": ""}


async def _draft(state: TrackerState, deps: TurnDeps) -> dict:
    turn = await drafting.draft_turn(deps, state["query"], state.get("draft_ids") or (),
                                     p.from_state(state.get("items")), state.get("outcomes") or {})
    return {"reply": turn.reply, "drafts": [d.to_dict() for d in turn.drafts], "route": ""}
