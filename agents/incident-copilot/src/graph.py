"""The incident-copilot LangGraph.

    START ─┬─ (a card exists, same incident) → followup ─┬─ answer / off-topic ─────→ END
           │                                             ├─ POST / POST_TAG (request) → END
           │                                             ├─ REFRESH ──────────→ fanout
           │                                             └─ new incident ─────→ intake
           └─ (no card, or a resumed question) → intake → incident ─┬─ reply / pause → END
                                                                    └─ fanout → assemble → summarize → render → END

A greeting never enters the graph (smalltalk.py). State is checkpointed per A2A contextId: the card, its
facts and the investigation (so a REFRESH keeps T0) survive between turns. The graph is compiled per turn
because its nodes close over that turn's dependencies; the checkpointer is shared. Pauses and the post
request leave the graph in the turn result; the walk runs outside it, its state on the A2A task.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import assemble
import card_render
import coverage as cov
import incident_step
import intake as intake_
import prompts
import records as r
import scoring
import smalltalk
import summarize as summarize_
from deps import TurnDeps
from fanout import fanout as run_fanout
from investigation import Investigation
from loops import ask_once
from windows import parse_time

log = logging.getLogger("incident_copilot.graph")

POST_HINT = ("Say \"post it\" to post this card to the incident's chat channel (I'll show it to you first), "
             "\"post it and tag the author\", or \"check again\" for a fresh look.")


class CopilotState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    preset: dict | None
    pause: dict | None
    post: dict | None
    target: dict
    alerting: dict
    investigation: dict      # persisted: incident, T0, service, repo
    data: dict
    statuses: list[dict]
    facts: dict
    summary: str
    fixes: dict
    card: dict               # persisted: text, hash, facts
    outcomes: dict           # persisted: card hash → posted / skipped / denied / blocked / not_posted


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: dict | None = None
    post: dict | None = None


class CopilotAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, preset: dict | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query) if preset is None else None
        if canned is not None:  # a greeting: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "route": "", "preset": preset, "pause": None, "post": None,
                 "data": {}, "statuses": [], "facts": {}, "summary": "", "fixes": {}}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return TurnResult(final.get("reply") or "", final.get("pause"), final.get("post"))

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        if not outcomes:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        current = await graph.aget_state(_config(context_id))
        merged = {**dict(current.values.get("outcomes") or {}), **dict(outcomes)}
        await graph.aupdate_state(_config(context_id), {"outcomes": merged, "post": None, "pause": None},
                                  as_node="render")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def followup(state: CopilotState) -> dict:
        return await _followup(state, deps)

    async def intake(state: CopilotState) -> dict:
        return await _intake(state, deps)

    async def incident(state: CopilotState) -> dict:
        return await _incident(state, deps)

    async def fanout(state: CopilotState) -> dict:
        inv = Investigation.from_dict(state["investigation"])
        result = await run_fanout(deps, inv)
        alerting = [cov.SourceStatus.from_dict(state["alerting"])] if state.get("alerting") else []
        return {"data": result.data, "statuses": [s.to_dict() for s in (*alerting, *result.statuses)]}

    async def assemble_node(state: CopilotState) -> dict:
        inv = Investigation.from_dict(state["investigation"])
        statuses = [cov.SourceStatus.from_dict(s) for s in state.get("statuses") or ()]
        return {"facts": assemble.facts(inv, state.get("data") or {}, statuses)}

    async def summarize(state: CopilotState) -> dict:
        sentence, fixes = await summarize_.summarize(deps, state["facts"])
        return {"summary": sentence, "fixes": fixes}

    async def render(state: CopilotState) -> dict:
        text = card_render.render(state["facts"], state["summary"], state.get("fixes") or {}, deps.now)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        card = {"text": text, "hash": digest, "facts": state["facts"]}
        return {"card": card, "reply": f"{text}\n\n{POST_HINT}", "route": "", "data": {}}

    graph = StateGraph(CopilotState)
    for name, node in (("followup", followup), ("intake", intake), ("incident", incident), ("fanout", fanout),
                       ("assemble", assemble_node), ("summarize", summarize), ("render", render)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "intake"])
    graph.add_conditional_edges("followup", _route, ["intake", "fanout", END])
    graph.add_conditional_edges("intake", _route, ["incident", END])
    graph.add_conditional_edges("incident", _route, ["fanout", END])
    graph.add_edge("fanout", "assemble")
    graph.add_edge("assemble", "summarize")
    graph.add_edge("summarize", "render")
    graph.add_edge("render", END)
    return graph


def _entry(state: CopilotState) -> str:
    return "followup" if state.get("card") and state.get("preset") is None else "intake"


def _route(state: CopilotState) -> str:
    return state.get("route") or END


def _target_dict(target: intake_.Target) -> dict:
    return {"incident_ref": target.incident_ref, "service": target.service, "repo": target.repo,
            "start": target.start.isoformat() if target.start else "",
            "lookback_min": int(target.lookback.total_seconds() // 60) if target.lookback else 0,
            "channel": target.channel}


def _target(data: Mapping) -> intake_.Target:
    lookback = int(data.get("lookback_min") or 0)
    return intake_.Target(incident_ref=data.get("incident_ref") or "", service=data.get("service") or "",
                          repo=data.get("repo") or "", start=parse_time(data["start"]) if data.get("start") else None,
                          lookback=timedelta(minutes=lookback) if lookback else None, channel=data.get("channel") or "")


async def _intake(state: CopilotState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    preset = state.get("preset") or {}
    if preset:  # a resumed question: the target was settled before the pause
        target = intake_.parse(state["query"], deps.now)
        if preset.get("start"):
            target = replace(target, start=parse_time(preset["start"]))
        return {"target": _target_dict(target), "route": "incident"}
    result = await intake_.intake(deps, state["query"])
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if result.status == "need_target":
        pause = incident_step.target_pause(state["query"])
        return {"pause": pause, "reply": pause["text"], "route": ""}
    return {"target": _target_dict(result.target), "route": "incident"}


async def _incident(state: CopilotState, deps: TurnDeps) -> dict:
    target, preset = _target(state["target"]), state.get("preset") or {}
    if preset.get("incident"):
        incident = r.from_dict(r.Incident, preset["incident"])
        inv = incident_step.investigation_for(incident, target, parse_time(incident.created_at), deps, True)
        return _investigating(inv, cov.SourceStatus("alerting", "ok"))
    if preset.get("start") and target.start is not None:
        status = cov.SourceStatus.from_dict(preset.get("alerting") or {"source": "alerting", "state": "missing"})
        inv = incident_step.investigation_for(incident_step.placeholder(target, target.start), target, target.start,
                                              deps, False)
        return _investigating(inv, status)
    outcome = await incident_step.find_incident(deps, target, state["query"])
    if outcome.reply is not None:
        return {"reply": outcome.reply, "route": ""}
    if outcome.pause is not None:
        return {"pause": outcome.pause, "reply": outcome.pause["text"], "route": ""}
    return _investigating(outcome.investigation, outcome.status)


def _investigating(inv: Investigation, status: cov.SourceStatus | None) -> dict:
    return {"investigation": inv.to_dict(), "alerting": status.to_dict() if status else {}, "route": "fanout"}


def _new_target(target: intake_.Target, inv: Investigation) -> bool:
    if target.incident_ref and target.incident_ref not in (inv.incident.id, inv.incident.url):
        return True
    return bool(target.service) and scoring.norm(target.service) != scoring.norm(inv.service)


async def _followup(state: CopilotState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    inv = Investigation.from_dict(state["investigation"])
    target = intake_.parse(state["query"], deps.now)
    if _new_target(target, inv):
        return {"route": "intake"}
    if target.repo and target.repo != inv.repo:  # "the repo is owner/name": re-check with that repository
        moved = replace(inv.at(deps.now), repo=target.repo, repo_source="message")
        return {"investigation": moved.to_dict(), "route": "fanout"}
    card = state["card"]
    listing = f"{card['text']}\n\n{card_render.details(card['facts'])}"
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_card(listing)}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": state["query"]}))
    word = text.strip().strip("`\"'. ").upper()
    if word.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if word.startswith(prompts.REFRESH):
        return {"investigation": inv.at(deps.now).to_dict(), "route": "fanout"}
    if word.startswith((prompts.POST_TAG, prompts.POST)):
        return {"post": _post_request(state, inv, target, word.startswith(prompts.POST_TAG)), "route": ""}
    return {"reply": text or prompts.UNREADABLE, "route": ""}


def _post_request(state: CopilotState, inv: Investigation, target: intake_.Target, tag: bool) -> dict:
    card = state["card"]
    suspects = card["facts"].get("suspects") or []
    outcomes = state.get("outcomes") or {}
    return {"card": card["text"], "card_hash": card["hash"], "chat_ref": inv.incident.chat_ref,
            "channel_hint": target.channel, "tag": tag, "suspect": suspects[0] if suspects else None,
            "posted": card["hash"] if outcomes.get(card["hash"]) == "posted" else ""}
