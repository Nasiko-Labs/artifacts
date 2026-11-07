"""The inbox-to-linear LangGraph: one turn's read side.

    START → intent ─┬─ off-topic → END (fixed decline)
                    └─ collect ─┬─ continue: the requests left from the last scan
                                ├─ envelope: `requests` from a previous step
                                ├─ paste: one pasted email
                                └─ scan (tool loop) → detect (LLM batches) → code checks
                       collect → batch (urgent first, then oldest; at most 5 proposals) → END

A greeting or small talk never enters the graph (smalltalk.py). State is checkpointed per A2A contextId,
so "continue" and the remembered team/tracker survive between turns and replicas. The graph is compiled
per turn because its nodes close over that turn's dependencies; the checkpointer is shared. The approval
walk (walk.py) runs outside the graph, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import detect
import issue
import parse
import prompts
import smalltalk
from deps import TurnDeps
from discovery import Catalog, discover
from llmcall import ask_once
from models import Named, Request
from paste import read_paste
from scan import scan
from walk import Setup

log = logging.getLogger("inbox_to_linear.graph")

MAX_PROPOSALS = 5


class InboxState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    pending: list[dict]   # requests found but not yet proposed: what "continue" picks up
    batch: list[dict]     # this turn: requests to propose
    report: dict          # this turn: counts and coverage for the envelope
    team: dict | None     # the team the user picked in this conversation
    family: str | None    # the tracker the user picked in this conversation


@dataclass(frozen=True)
class TurnResult:
    reply: str = ""
    setup: Setup | None = None
    catalog: Catalog | None = None


class _Scope:
    def __init__(self, deps: TurnDeps, ask: parse.Ask):
        self.deps = deps
        self.ask = ask
        self.catalog: Catalog | None = None


class InboxAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, ask: parse.Ask, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(ask.text)
        if canned is not None:  # greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if ask.wants_assign:
            return TurnResult(prompts.ASSIGN_DEFERRED)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        scope = _Scope(deps, ask)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"query": ask.text, "reply": "", "route": "", "batch": [], "report": {}}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        if final.get("reply"):
            return TurnResult(final["reply"])
        team = final.get("team")
        setup = Setup(requests=tuple(Request.from_dict(r) for r in final.get("batch") or ()), team_hint=ask.team_hint,
                      team=Named.from_dict(team) if team else None, family=final.get("family"),
                      dry_run=ask.dry_run, json_mode=ask.wants_json, report=dict(final.get("report") or {}))
        return TurnResult(setup=setup, catalog=scope.catalog or Catalog(listed=()))

    async def remember(self, context_id: str, values: Mapping[str, Any]) -> None:
        """Store the conversation's choices (team, tracker) for later turns."""
        if not values:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), dict(values), as_node="batch")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def intent(state: InboxState) -> dict:
        return await _intent(scope)

    async def collect(state: InboxState) -> dict:
        return await _collect(state, scope)

    async def batch(state: InboxState) -> dict:
        return _batch(state)

    graph = StateGraph(InboxState)
    for name, node in (("intent", intent), ("collect", collect), ("batch", batch)):
        graph.add_node(name, node)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges("intent", _route, ["collect", END])
    graph.add_conditional_edges("collect", _route, ["batch", END])
    graph.add_edge("batch", END)
    return graph


def _route(state: InboxState) -> str:
    return state.get("route") or END


async def _intent(scope: _Scope) -> dict:
    ask = scope.ask
    if ask.is_continue or ask.envelope_requests is not None or read_paste(ask.text) is not None:
        return {"route": "collect"}
    answer = await ask_once(scope.deps, ({"role": "system", "content": prompts.INTENT_SYSTEM},
                                         {"role": "user", "content": prompts.strip_markers(ask.text)}))
    if answer.strip().upper().startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"route": "collect"}


async def _collect(state: InboxState, scope: _Scope) -> dict:
    ask = scope.ask
    if ask.is_continue:
        pending = list(state.get("pending") or ())
        if not pending:
            return {"reply": prompts.NOTHING_TO_CONTINUE, "route": ""}
        await _catalog(scope)
        return {"batch": pending, "pending": [], "report": {"pasted": False}, "route": "batch"}
    if ask.envelope_requests is not None:
        await _catalog(scope)
        requests = [r for r in (Request.from_dict(x) for x in ask.envelope_requests) if r.ask_quote]
        return {"batch": [r.to_dict() for r in requests], "report": {"pasted": False}, "route": "batch"}
    pasted = read_paste(ask.text)
    if pasted is not None:
        await _catalog(scope)
        found, dropped = await detect.detect(scope.deps, (pasted,), ask.text)
        if not found:
            return {"reply": prompts.PASTE_NOT_A_REQUEST, "route": ""}
        requests = [Request.from_dict({**r.to_dict(), "pasted": True}) for r in found]
        return {"batch": [r.to_dict() for r in requests], "report": {"pasted": True, "dropped": dropped},
                "route": "batch"}
    return await _scan(scope)


async def _catalog(scope: _Scope) -> str | None:
    catalog, failure = await discover(scope.deps)
    scope.catalog = catalog
    return failure


async def _scan(scope: _Scope) -> dict:
    failure = await _catalog(scope)
    if failure is not None:
        return {"reply": failure, "route": ""}
    window = scope.ask.window_days
    outcome = await scan(scope.deps, scope.catalog, scope.ask.text, window)
    if outcome.reply is not None:
        return {"reply": outcome.reply, "route": ""}
    found, refused = await detect.detect(scope.deps, outcome.threads, outcome.seen_text) if outcome.threads else ((), 0)
    if not found:
        return {"reply": "\n\n".join([prompts.nothing_found(window), *outcome.notes]), "route": ""}
    return {"batch": [r.to_dict() for r in found], "route": "batch",
            "report": {"pasted": False, "dropped": outcome.dropped + refused, "coverage": list(outcome.notes),
                       "window_days": window}}


def _batch(state: InboxState) -> dict:
    """Urgent first, then oldest; every already-tracked one plus at most 5 proposals; the rest wait."""
    requests = issue.ordered([Request.from_dict(r) for r in state.get("batch") or ()])
    tracked = [r for r in requests if r.tracked_key]
    untracked = [r for r in requests if not r.tracked_key]
    now, later = untracked[:MAX_PROPOSALS], untracked[MAX_PROPOSALS:]
    report = {**dict(state.get("report") or {}), "remaining": len(later)}
    return {"batch": [r.to_dict() for r in (*now, *tracked)], "pending": [r.to_dict() for r in later],
            "report": report, "route": ""}
