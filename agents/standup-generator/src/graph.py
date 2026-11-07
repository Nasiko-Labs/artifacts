"""The standup LangGraph.

    START ─┬─ (a standup exists here and the message is a follow-up) → followup ─┬─ answered ──→ END
           │                                                                   └─ refetch ─┐
           └─ (new request, or the answer to the scope question) ──────────────→ pull ←────┘ → END

Before the graph, in code: a greeting gets the canned intro (zero calls) and a request without a
traceparent gets a fixed reply (zero calls).

State is checkpointed per A2A contextId (thread_id): the checked records of the last standup, the view
(only / shorter / reviews / chat-ready), the remembered repositories and project, and the identity
corrections. The graph is compiled per turn because its nodes close over that turn's dependencies;
the checkpointer is shared.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import followup
import prompts
import smalltalk
from deps import TurnDeps
from intake import Request, with_scope_answer
from pull import fetch, start
from render import View
from report import Outcome, labels, render_stored
from window import Window

__all__ = ["StandupAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("standup.graph")


class StandupState(TypedDict, total=False):
    query: str
    reply: str
    data: dict | None
    pause: dict | None
    route: str
    resume: dict | None  # the paused scope question this message answers
    refetch: dict | None  # a follow-up whose window starts before what was read
    stored: dict
    view: dict
    scope: dict


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pause: dict | None = None


class StandupAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return TurnResult(canned)
        return await self._run({"query": query, "resume": None}, context_id, deps)

    async def resume(self, pending: dict, answer: str, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run({"query": answer, "resume": pending}, context_id, deps)

    async def _run(self, start_state: dict, context_id: str, deps: TurnDeps) -> TurnResult:
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        fresh = {"reply": "", "data": None, "pause": None, "route": "", "refetch": None}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke({**fresh, **start_state}, config, durability="exit")
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("data"), final.get("pause"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup_node(state: StandupState) -> dict:
        return await _followup(state, deps)

    async def pull_node(state: StandupState) -> dict:
        return await _pull(state, deps)

    def first(state: StandupState) -> str:
        if state.get("resume") or not state.get("stored"):
            return "pull"
        parsed = followup.parse(state["query"], labels(state["stored"]), View.from_dict(state.get("view") or {}),
                                deps.now)
        return "followup" if parsed is not None else "pull"

    graph = StateGraph(StandupState)
    graph.add_node("followup", followup_node)
    graph.add_node("pull", pull_node)
    graph.add_conditional_edges(START, first, ["followup", "pull"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["pull", END])
    graph.add_edge("pull", END)
    return graph


async def _followup(state: StandupState, deps: TurnDeps) -> dict:
    stored = state["stored"]
    parsed = followup.parse(state["query"], labels(stored), View.from_dict(state.get("view") or {}), deps.now)
    if parsed is None:
        return {"route": "pull"}
    corrections = dict(stored.get("corrections") or {})
    if parsed.correction is not None:
        label, alias, _side = parsed.correction
        corrections = {**corrections, label.casefold(): [*corrections.get(label.casefold(), []), alias]}
    window, refetch = followup.window_after(Window.from_dict(stored["window"]),
                                            datetime.fromisoformat(stored["fetched_since"]), parsed.window_since)
    if refetch:
        log.info("follow-up window starts before what was read: reading again")
        return {"route": "pull", "refetch": {"request": stored["request"], "since": window.since.isoformat(),
                                             "view": parsed.view.to_dict(), "corrections": corrections,
                                             "json": parsed.json_mode}}
    updated = {**stored, "window": window.to_dict(), "corrections": corrections}
    outcome = await render_stored(deps, updated, parsed.view, parsed.json_mode, with_gist=False)
    return {**_reply(outcome), "stored": outcome.stored, "view": parsed.view.to_dict(), "route": ""}


async def _pull(state: StandupState, deps: TurnDeps) -> dict:
    query = state["query"]
    pending, again = state.get("resume"), state.get("refetch")
    if pending:
        request = with_scope_answer(Request.from_dict(pending["request"]), query)
        view = View.from_dict(pending.get("view") or {})
        outcome = await fetch(deps, request, Window.from_dict(pending["window"]), pending.get("query", ""), view)
    elif again:
        view = View.from_dict(again["view"])
        window = Window(datetime.fromisoformat(again["since"]), deps.now)
        text = query if not again.get("json") else f"{query} as json"
        outcome = await fetch(deps, Request.from_dict(again["request"]), window, text, view, again["corrections"])
    else:
        view = followup.initial_view(query)
        remembered = Request.from_dict(state["scope"]) if state.get("scope") else None
        outcome = await start(deps, query, remembered, view)
    update = {**_reply(outcome), "route": "", "resume": None, "refetch": None}
    if outcome.stored is None:
        return update
    request = Request.from_dict(outcome.stored["request"])
    scope = {"repos": list(request.repos), "project": request.project, "any_scope": request.any_scope}
    return {**update, "stored": outcome.stored, "view": view.to_dict(), "scope": scope}


def _reply(outcome: Outcome) -> dict:
    return {"reply": outcome.reply, "data": outcome.data, "pause": outcome.pause}
