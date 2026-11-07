"""The feedback-to-roadmap LangGraph.

    START ─┬─ (a result is stored and this is not a new pull) → followup ─┬─ answered → END
           │                                                              └─ NEEDS_PULL ─┐
           └─ (nothing yet, or a new window/channel/paste) ───────────────→ pull ←───────┘
      pull: envelope input → adopt → END | scope check → gather (desk + chat + paste) → analyse → END

A greeting never enters the graph (smalltalk.py); a missing traceparent ends the turn before any call. State
is checkpointed per A2A contextId; the graph is compiled per turn because its nodes close over that turn's
dependencies. One pull per turn; follow-ups answer from the stored result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import records
import render
import replies
import smalltalk
import window as windows
from analyse import analyse
from budget import TurnLimitReached
from deps import TurnDeps
from followup import followup_step, wants_new_pull
from gather import gather
from parse_paste import Request, read
from reader import TurnReader, ask_once
from report import adopt

__all__ = ["RoadmapAgent", "TurnDeps", "TurnResult"]


class RoadmapState(TypedDict, total=False):
    reply: str
    data: dict | None
    result: dict | None        # the last envelope (stored across turns)
    last_pull: str             # ISO date of the last pull in this conversation
    route: str
    skip_scope: bool


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None


class RoadmapAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, text: str, context_id: str, deps: TurnDeps, json_meta: bool = False) -> TurnResult:
        canned = smalltalk.reply_to(text)
        if canned is not None:  # greeting / small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(replies.NO_TRACE_CONTEXT)
        request = read(text, deps.config.max_paste_chars, json_meta)
        graph = _build_graph(deps, request).compile(checkpointer=self._checkpointer)
        final = await graph.ainvoke({"reply": "", "data": None, "route": "", "skip_scope": False},
                                    {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", final.get("data"))


def _build_graph(deps: TurnDeps, request: Request) -> StateGraph:
    async def followup(state: RoadmapState) -> dict:
        done = await followup_step(deps, state["result"], request)
        if done.pull:
            return {"route": "pull", "skip_scope": True}
        return {"reply": done.reply, "data": done.data, "route": ""}

    async def pull(state: RoadmapState) -> dict:
        return await _pull(deps, request, state)

    def entry(state: RoadmapState) -> str:
        return "followup" if state.get("result") and not wants_new_pull(request) else "pull"

    graph = StateGraph(RoadmapState)
    graph.add_node("followup", followup)
    graph.add_node("pull", pull)
    graph.add_conditional_edges(START, entry, ["followup", "pull"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["pull", END])
    graph.add_edge("pull", END)
    return graph


async def _pull(deps: TurnDeps, request: Request, state: RoadmapState) -> dict:
    if request.envelope is not None:
        env = adopt(request.envelope)
        if env is None:
            return {"reply": "That JSON isn't a feedback-themes result I can read, so I didn't use it.", "route": ""}
        return _finish(env, request, state.get("last_pull") or "")
    if not state.get("skip_scope"):
        try:
            scoped = records.parse(records.RawScope, await ask_once(deps, prompts.scope_messages(request.text)))
        except TurnLimitReached:
            return {"reply": replies.STEP_LIMIT_NOTE, "route": ""}
        if scoped is not None and scoped.intent.strip().casefold() == "off_topic":
            return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    last = state.get("last_pull")
    cfg = deps.config
    window = windows.parse(request.text, deps.today, cfg.window_days, cfg.min_window_days, cfg.max_window_days,
                           date.fromisoformat(last) if last else None)
    turn = TurnReader(deps)
    gathered = await gather(deps, turn, request, window)
    if gathered.fatal:
        return {"reply": gathered.fatal, "route": ""}
    notes = "\n".join(f"- {n}" for n in gathered.facts.notes)
    if not gathered.facts.read_counts:
        return {"reply": replies.NO_SOURCES_PASTE_HINT + (f"\n\n{notes}" if notes else ""), "route": ""}
    if len(gathered.items) < 3:
        found = f"I found {len(gathered.items)} item(s) from {window.label()}."
        return {"reply": f"{replies.TOO_FEW} {found}" + (f"\n\n{notes}" if notes else ""), "route": ""}
    env = await analyse(deps, turn, request, window, gathered.items, gathered.facts)
    return _finish(env, request, deps.today.isoformat())


def _finish(env: dict, request: Request, last_pull: str) -> dict:
    reply = render.as_json(env) if request.json_mode else render.report(env)
    return {"reply": reply, "data": env, "result": env, "last_pull": last_pull, "route": ""}
