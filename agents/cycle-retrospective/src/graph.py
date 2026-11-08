"""The cycle-retrospective LangGraph.

    START ─┬─ (a retro was given in this context) → followup ─┬─ answered / json / post card → END
           │                                                  └─ NEEDS_PULL ─┐
           └─ (nothing yet, or a team was just picked) ─────────────→ cycle ←─┘
      cycle ─┬─ reply / "Which team?" pause → END
             └─ issues ─┬─ reply → END
                        └─ evidence (largest spills) → finish: reasons, metrics, prompts, render (+ post card) → END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId; the graph is
compiled per turn because its nodes close over that turn's dependencies and its one TurnReader."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import post
import request
import smalltalk
from deps import TurnDeps
from finish import finish_step
from metrics import split
from reader import TurnReader
from records import Cycle, Issue, Team, from_state, to_state
from steps_cycle import cycle_step
from steps_evidence import evidence_step
from steps_followup import followup_step
from steps_issues import issues_step

__all__ = ["RetroAgent", "TurnDeps", "TurnResult"]


class RetroState(TypedDict, total=False):
    query: str
    team_choice: str           # this turn: the team picked on the "Which team?" card
    reply: str
    route: str
    pause: dict | None
    envelope: dict | None
    req: dict
    team: dict | None
    cycle: dict | None
    issues: list[dict]
    evidence: list[dict]
    quality: dict              # what the checks saw this turn (retro.Quality fields)
    notes: list[str]
    retro: dict | None         # the last finished retro, kept for follow-ups
    markdown: str
    channel: str


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: post.Pause | None = None
    envelope: dict | None = None


class RetroAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, *, team_choice: str = "") -> TurnResult:
        if not team_choice:
            canned = smalltalk.reply_to(query)
            if canned is not None:  # greeting / small talk: no tool call, no LLM call, state untouched
                return TurnResult(canned)
        graph = _build_graph(deps, TurnReader(deps)).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "team_choice": team_choice, "reply": "", "route": "", "pause": None,
                 "envelope": None, "notes": [], "quality": {}}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        pause = final.get("pause")
        return TurnResult(final.get("reply") or "", post.Pause(**pause) if pause else None, final.get("envelope"))


def _build_graph(deps: TurnDeps, turn: TurnReader) -> StateGraph:
    async def followup(state: RetroState) -> dict:
        return await followup_step(deps, state)

    async def cycle(state: RetroState) -> dict:
        return await _cycle(deps, turn, state)

    async def issues(state: RetroState) -> dict:
        return await _issues(deps, turn, state)

    async def evidence(state: RetroState) -> dict:
        return await _evidence(deps, turn, state)

    async def finish(state: RetroState) -> dict:
        return await finish_step(deps, state)

    graph = StateGraph(RetroState)
    for name, node in (("followup", followup), ("cycle", cycle), ("issues", issues), ("evidence", evidence),
                       ("finish", finish)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "cycle"])
    graph.add_conditional_edges("followup", _route, ["cycle", END])
    graph.add_conditional_edges("cycle", _route, ["issues", END])
    graph.add_conditional_edges("issues", _route, ["evidence", END])
    graph.add_edge("evidence", "finish")
    graph.add_edge("finish", END)
    return graph


def _entry(state: RetroState) -> str:
    return "followup" if state.get("retro") and not state.get("team_choice") else "cycle"


def _route(state: RetroState) -> str:
    return state.get("route") or END


async def _cycle(deps: TurnDeps, turn: TurnReader, state: RetroState) -> dict:
    query = state["query"]
    req = request.parse(query)
    if state.get("team_choice"):
        req = request.Request(**{**asdict(req), "team": state["team_choice"]})
    found = await cycle_step(deps, turn, query, req)
    if found.teams_to_pick:
        pause = post.team_pause(found.teams_to_pick, query, found.notes)
        return {"pause": asdict(pause), "reply": "", "route": ""}
    if found.reply is not None:  # a refusal or nothing found keeps the previous retro
        return {"reply": found.reply, "route": ""}
    quality = {"extracted": 0, "dropped": found.dropped, "salvaged": found.salvaged}
    return {"req": asdict(req), "team": asdict(found.team), "cycle": asdict(found.cycle), "notes": list(found.notes),
            "quality": quality, "channel": req.channel or state.get("channel") or "", "route": "issues"}


async def _issues(deps: TurnDeps, turn: TurnReader, state: RetroState) -> dict:
    found = await issues_step(deps, turn, from_state(Team, state["team"]), from_state(Cycle, state["cycle"]))
    if found.reply is not None:
        return {"reply": found.reply, "route": ""}
    q = state.get("quality") or {}
    quality = {**q, "extracted": q.get("extracted", 0) + found.extracted, "dropped": q.get("dropped", 0) + found.dropped,
               "capped": bool(found.capped), "salvaged": bool(q.get("salvaged")) or found.salvaged}
    return {"issues": to_state(found.issues), "quality": quality, "notes": [*state.get("notes", []), *found.notes],
            "route": "evidence"}


async def _evidence(deps: TurnDeps, turn: TurnReader, state: RetroState) -> dict:
    issues = from_state(Issue, state.get("issues") or [])
    parts = split(issues, from_state(Cycle, state["cycle"]), deps.now, deps.config.planned_grace_hours)
    largest = parts.spilled[:deps.config.max_evidence_issues]
    req = state.get("req") or {}
    found = await evidence_step(deps, turn, largest, from_state(Cycle, state["cycle"]),
                                tuple(req.get("evidence_channels") or ()))
    q = state.get("quality") or {}
    quality = {**q, "extracted": q.get("extracted", 0) + found.extracted, "dropped": q.get("dropped", 0) + found.dropped,
               "salvaged": bool(q.get("salvaged")) or found.salvaged, "chat_searched": found.chat_searched,
               "gaps": bool(q.get("gaps")) or not found.comments_read}
    notes = [*state.get("notes", []), *found.notes]
    if len(parts.spilled) > len(largest):
        notes.append(f"Reasons were searched for the {len(largest)} largest spilled issues only; the other "
                     f"{len(parts.spilled) - len(largest)} show no reason found.")
    return {"evidence": to_state(found.evidence), "quality": quality, "notes": notes}
