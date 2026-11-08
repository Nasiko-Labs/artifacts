"""The scope-creep LangGraph.

    START ─┬─ (a report was given in this context) → followup ─┬─ answered / recomputed ─→ END
           │                                                   └─ NEEDS_PULL ─┐
           └─ (no report yet, or a team was just picked) ─────────────→ pull ←┘ → END

Before the graph, in code: a greeting gets the canned intro with zero tool and LLM calls.

State is checkpointed per A2A contextId (thread_id). It keeps the checked snapshot (records, never raw
tool output) so a follow-up that only changes the grace period is recomputed in code with no new pull,
the rendered report that other follow-ups are answered from, and the added keys of the last run for
"since I last checked". The graph is compiled per turn because its nodes close over that turn's
dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import render
import run
import scope
import smalltalk
from deps import TurnDeps
from records import Snapshot
from request import ScopeRequest, parse
from steps import ask_once

__all__ = ["ScopeAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("scope_creep.graph")


class ScopeState(TypedDict, total=False):
    query: str
    as_json: bool
    team_override: str | None
    reply: str
    route: str
    envelope: dict | None
    pause: dict | None
    snapshot: dict | None  # Snapshot.to_json() of the last pull
    grace: int | None  # the grace override the last report used (None = default)
    notes: list[str]
    report: str
    added_keys: list[str]
    sprint_id: str
    team: str | None  # the team the last report was for, reused by a follow-up rerun


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None
    pause: dict | None = None


class ScopeAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, as_json: bool = False,
                   team_override: str | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query) if team_override is None else None
        if canned is not None:
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        fresh = {"query": query, "as_json": as_json, "team_override": team_override, "reply": "", "route": "",
                 "envelope": None, "pause": None}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, config, durability="exit")
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("envelope"), final.get("pause"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: ScopeState) -> dict:
        return await _followup(state, deps)

    async def pull(state: ScopeState) -> dict:
        return await _pull(state, deps)

    def first(state: ScopeState) -> str:
        return "followup" if state.get("snapshot") and not state.get("team_override") else "pull"

    graph = StateGraph(ScopeState)
    graph.add_node("followup", followup)
    graph.add_node("pull", pull)
    graph.add_conditional_edges(START, first, ["followup", "pull"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["pull", END])
    graph.add_edge("pull", END)
    return graph


def _request(state: ScopeState) -> ScopeRequest:
    return parse(state["query"], as_json=bool(state.get("as_json")))


async def _pull(state: ScopeState, deps: TurnDeps) -> dict:
    req = _request(state)
    # A rerun asked for by a follow-up keeps the team of the report it replaces, unless a new one is named.
    rerun_team = state.get("team") if state.get("route") == "pull" and not req.team else None
    outcome = await run.pull(deps, req, state.get("team_override") or rerun_team)
    if outcome.pause is not None:
        return {"reply": "", "pause": dict(outcome.pause), "route": ""}
    if outcome.snapshot is None:
        return {"reply": outcome.reply, "route": ""}
    prior = state.get("added_keys") if state.get("sprint_id") == outcome.snapshot.sprint.id else None
    team = state.get("team_override") or rerun_team or req.team or outcome.snapshot.sprint.team or None
    shown = _present(outcome.snapshot, req.grace_hours, list(outcome.notes), deps, req.as_json, prior)
    return {**shown, "team": team}


def _present(snap: Snapshot, grace: int | None, notes: list[str], deps: TurnDeps, as_json: bool,
             prior_added: list[str] | None = None) -> dict:
    report = scope.compute(snap, scope.clamp_grace(grace), deps.now)
    keys = [a.issue_key for a in report.added]
    markdown = render.markdown(report, notes, _since_last(keys, prior_added))
    envelope = render.envelope(report, notes)
    reply = render.json_block(envelope) if as_json else markdown
    return {"reply": reply, "envelope": envelope, "snapshot": snap.to_json(), "grace": grace, "notes": notes,
            "report": markdown, "added_keys": keys, "sprint_id": snap.sprint.id, "route": ""}


def _since_last(keys: list[str], prior: list[str] | None) -> str:
    if prior is None:
        return ""
    new = [k for k in keys if k not in prior]
    if not new:
        return "Since I last checked: no new additions."
    return f"Since I last checked: {len(new)} more added ({', '.join(new)})."


async def _followup(state: ScopeState, deps: TurnDeps) -> dict:
    req = _request(state)
    if req.looks_pasted or req.from_envelope:
        return {"route": "pull"}
    snap = Snapshot.from_json(state["snapshot"])
    if _only_regrace(req):
        log.info("follow-up recomputed with grace %s h, no new pull", req.grace_hours)
        return _present(snap, req.grace_hours, list(state.get("notes") or ()), deps, req.as_json)
    if req.as_json and _is_bare_json_ask(req.text):
        return _present(snap, state.get("grace"), list(state.get("notes") or ()), deps, True)
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state.get('report') or '')}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": req.text}))
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "pull"}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


def _only_regrace(req: ScopeRequest) -> bool:
    """A grace change for the same sprint: no team, no other sprint, nothing pasted."""
    return req.grace_hours is not None and req.team is None and req.selector == "current"


def _is_bare_json_ask(text: str) -> bool:
    return len(text.split()) <= 6
