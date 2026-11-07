"""The slack-thread-to-issue LangGraph: one turn's read side.

    START → intent ─┬─ off-topic → END (fixed decline)
                    └─ collect ─┬─ continue: the findings left from the last scan
                                ├─ paste: one pasted thread → judge
                                └─ scan (tool loop) → candidates (code) → judge (LLM per 5 threads) → gates (code)
                       collect → batch (bugs first, then newest; at most 5 proposals) → END

A greeting, a "watch" ask or an assignment ask never enters the graph. State is checkpointed per A2A
contextId, so "continue" and the remembered team/tracker survive between turns and replicas. The graph is
compiled per turn because its nodes close over that turn's dependencies; the checkpointer is shared. The
approval walk (walk.py) runs outside the graph, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import issue
import judge
import parse
import prompts
import smalltalk
from deps import TurnDeps
from discovery import Catalog, discover
from llmcall import ask_once
from models import Finding, Named
from paste import read_paste
from scan import scan
from walk import Setup

log = logging.getLogger("thread_to_issue.graph")

MAX_PROPOSALS = 5


class ThreadState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    pending: list[dict]   # findings not yet proposed: what "continue" picks up
    batch: list[dict]     # this turn: findings to propose
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


def fixed_reply(ask: parse.Ask) -> str | None:
    """A reply decided in code before any call: small talk, a "watch" ask, an assignment ask."""
    canned = smalltalk.reply_to(ask.text)
    if canned is not None:
        return canned
    if ask.from_envelope or read_paste(ask.text) is not None:
        return None
    if ask.wants_watch:
        return prompts.WATCH_DEFERRED
    if ask.wants_assign:
        return prompts.ASSIGN_DEFERRED
    return None


class ThreadAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, ask: parse.Ask, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = fixed_reply(ask)
        if canned is not None:  # no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        scope = _Scope(deps, ask)
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        # `report` is kept: "continue" reuses the last scan's counts; every other path sets it afresh.
        fresh = {"query": ask.text, "reply": "", "route": "", "batch": []}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        if final.get("reply"):
            return TurnResult(final["reply"])
        team = final.get("team")
        setup = Setup(findings=tuple(Finding.from_dict(f) for f in final.get("batch") or ()), team_hint=ask.team_hint,
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
    async def intent(state: ThreadState) -> dict:
        return await _intent(scope)

    async def collect(state: ThreadState) -> dict:
        return await _collect(state, scope)

    async def batch(state: ThreadState) -> dict:
        return _batch(state)

    graph = StateGraph(ThreadState)
    for name, node in (("intent", intent), ("collect", collect), ("batch", batch)):
        graph.add_node(name, node)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges("intent", _route, ["collect", END])
    graph.add_conditional_edges("collect", _route, ["batch", END])
    graph.add_edge("batch", END)
    return graph


def _route(state: ThreadState) -> str:
    return state.get("route") or END


async def _intent(scope: _Scope) -> dict:
    ask = scope.ask
    if ask.is_continue or ask.from_envelope or read_paste(ask.text) is not None:
        return {"route": "collect"}
    answer = await ask_once(scope.deps, ({"role": "system", "content": prompts.INTENT_SYSTEM},
                                         {"role": "user", "content": prompts.strip_markers(ask.text)}))
    if answer.strip().upper().startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"route": "collect"}


async def _collect(state: ThreadState, scope: _Scope) -> dict:
    ask = scope.ask
    if ask.is_continue:
        pending = list(state.get("pending") or ())
        if not pending:
            return {"reply": prompts.NOTHING_TO_CONTINUE, "route": ""}
        await _catalog(scope)
        report = {**dict(state.get("report") or {}), "remaining": 0}
        return {"batch": pending, "pending": [], "report": report, "route": "batch"}
    pasted = read_paste(ask.text)
    if pasted is not None:
        await _catalog(scope)
        judged = await judge.judge(scope.deps, (pasted,), ask.text)
        if not judged.findings:
            return {"reply": prompts.PASTE_NO_ACTION, "route": ""}
        return {"batch": [f.to_dict() for f in judged.findings], "route": "batch",
                "report": {"pasted": True, "dropped": judged.refused, "threads": 1,
                           "channels": [pasted.channel] if pasted.channel else []}}
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
    outcome = await scan(scope.deps, scope.catalog, scope.ask.text, window, scope.ask.channels)
    if outcome.reply is not None:
        return {"reply": outcome.reply, "route": ""}
    judged = await judge.judge(scope.deps, outcome.threads, outcome.seen_text) if outcome.threads else judge.Judged()
    notes = (*outcome.notes, *((prompts.TIME_LIMIT_NOTE,) if judged.timed_out else ()))
    if not judged.findings:
        return {"reply": "\n\n".join([prompts.nothing_found(window, len(outcome.threads)), *notes]), "route": ""}
    return {"batch": [f.to_dict() for f in judged.findings], "route": "batch",
            "report": {"pasted": False, "dropped": outcome.dropped + judged.refused, "coverage": list(notes),
                       "window_days": window, "channels": list(outcome.channels), "threads": len(outcome.threads)}}


def _batch(state: ThreadState) -> dict:
    """Bugs first, then newest; every already-tracked one plus at most 5 proposals; the rest wait."""
    findings = issue.ordered([Finding.from_dict(f) for f in state.get("batch") or ()])
    now, later = findings[:MAX_PROPOSALS], findings[MAX_PROPOSALS:]
    report = {**dict(state.get("report") or {}), "remaining": len(later)}
    return {"batch": [f.to_dict() for f in now], "pending": [f.to_dict() for f in later], "report": report,
            "route": ""}
