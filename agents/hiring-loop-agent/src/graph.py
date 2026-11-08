"""The hiring-loop LangGraph.

    START ─┬─ (a debrief exists in this context, nothing new pasted) → followup ─┬─ answered ──→ END
           │                                                                    └─ NEEDS_PULL ─┐
           └─ (otherwise) ──────────────────────────────────────────────────→ debrief ←────────┘ → END

Before the graph, in code and with zero calls: a greeting gets the intro; a request to compare or rank
candidates, or for a hire/no-hire decision, gets a fixed decline (request.py). Only the request part of the
message (the text before the first pasted block) is checked, never the feedback.

    debrief: pasted blocks (parsed in code) → extract → analyse → render
             unstructured paste → one split call → the same
             a name only → email and documents (collect.py) → maybe "Which candidate?" → the same

State is checkpointed per A2A contextId so a follow-up is answered from the rendered debrief. The graph is
compiled per turn because its nodes close over that turn's input and dependencies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import parse_blocks
import prompts
import request
import smalltalk
from checks import scrub
from collect import collect
from debrief import build
from deps import TurnDeps
from extract import intake, split
from inputs import Incoming
from pauses import Pending, interpret
from steps import ask_once

log = logging.getLogger("hiring_loop.graph")

SPLIT_MIN_CHARS = 300
GUARD_CHARS = 400


class LoopState(TypedDict, total=False):
    reply: str
    data: dict
    pause: dict
    route: str
    report: str   # the last debrief shown in this context: what follow-ups are answered from
    candidate: str


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict | None = None
    pause: Pending | None = None


@dataclass(frozen=True)
class _Turn:
    incoming: Incoming
    parsed: parse_blocks.Parsed
    req: request.Request
    chosen: str = ""


class HiringLoopAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            return TurnResult(canned)
        parsed = parse_blocks.parse(incoming.text)
        asked = parsed.instruction if parsed.blocks else incoming.text[:GUARD_CHARS]
        if request.asks_to_rank(asked):
            log.info("declined a comparison or ranking request")
            return TurnResult(prompts.NO_RANKING_DECLINE)
        if request.asks_for_decision(asked):
            log.info("declined a hire/no-hire request")
            return TurnResult(prompts.NO_DECISION_NOTE)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        return await self._run(_Turn(incoming, parsed, request.read(asked, incoming.wants_json)), context_id, deps)

    async def resume(self, pending: Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        chosen = interpret(pending, incoming.text)
        if chosen is None:
            return TurnResult(prompts.CANCELLED)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        original = Incoming(pending.instruction, wants_json=pending.wants_json)
        req = request.read(pending.instruction, pending.wants_json)
        return await self._run(_Turn(original, parse_blocks.Parsed(pending.instruction, ()), req, chosen),
                               context_id, deps)

    async def _run(self, turn: _Turn, context_id: str, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(turn, deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        final = await graph.ainvoke({"reply": "", "route": "", "pause": {}, "data": {}}, config, durability="exit")
        pause = Pending.from_metadata(final.get("pause") or None)
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("data") or None, pause)


def _build_graph(turn: _Turn, deps: TurnDeps) -> StateGraph:
    async def followup(state: LoopState) -> dict:
        return await _followup(state, turn, deps)

    async def debrief(state: LoopState) -> dict:
        return await _debrief(turn, deps)

    graph = StateGraph(LoopState)
    graph.add_node("followup", followup)
    graph.add_node("debrief", debrief)
    graph.add_conditional_edges(START, lambda s: "followup" if _is_followup(s, turn) else "debrief",
                                ["followup", "debrief"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["debrief", END])
    graph.add_edge("debrief", END)
    return graph


def _is_followup(state: LoopState, turn: _Turn) -> bool:
    """A question about the debrief already shown: nothing new pasted, no other candidate named, no source asked."""
    if not state.get("report") or turn.parsed.blocks or turn.chosen or turn.req.wants_mail or turn.req.wants_docs:
        return False
    named = turn.req.candidate.casefold()
    return not named or named in (state.get("candidate") or "").casefold()


async def _followup(state: LoopState, turn: _Turn, deps: TurnDeps) -> dict:
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state['report'])}"
    text = await ask_once(deps, system, turn.incoming.text)
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "debrief"}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": "", "data": {}}
    if text.startswith(prompts.DECISION):
        return {"reply": prompts.NO_DECISION_NOTE, "route": "", "data": {}}
    cleaned, replaced = scrub(text)
    if replaced:
        log.info("replaced %d follow-up sentence(s) that carried decision wording", replaced)
    return {"reply": cleaned or prompts.UNREADABLE_ANSWER, "route": "", "data": {}}


async def _debrief(turn: _Turn, deps: TurnDeps) -> dict:
    if turn.parsed.blocks:
        candidate = turn.parsed.candidate or turn.req.candidate
        return _shown(await build(deps, turn.parsed.blocks, turn.req, candidate), candidate)
    text = turn.incoming.text
    if not turn.chosen and len(text) >= SPLIT_MIN_CHARS:
        found = await split(deps, text)
        if found.off_topic:
            return _said(smalltalk.OFF_TOPIC_REPLY)
        if not found.blocks:
            return _said(prompts.NEED_INPUT)
        candidate = found.candidate or turn.req.candidate
        return _shown(await build(deps, found.blocks, turn.req, candidate, model_split=True), candidate)
    candidate = turn.chosen or await _candidate(turn, deps)
    if candidate is None:
        return _said(smalltalk.OFF_TOPIC_REPLY)
    if not candidate:
        return _said(prompts.NEED_INPUT)
    return await _from_sources(turn, deps, candidate)


async def _candidate(turn: _Turn, deps: TurnDeps) -> str | None:
    """The candidate's name ("" when none), or None when the request is out of scope."""
    if turn.req.in_scope and turn.req.candidate:
        return turn.req.candidate
    found = await intake(deps, turn.incoming.text)
    if found.off_topic:
        return None
    return found.candidate or turn.req.candidate


async def _from_sources(turn: _Turn, deps: TurnDeps, candidate: str) -> dict:
    got = await collect(deps, turn.req, candidate, chosen=bool(turn.chosen))
    if got.options:
        pending = Pending(turn.incoming.text, got.options, turn.req.wants_json)
        return {"reply": "", "pause": pending.to_metadata(), "route": "", "data": {}}
    if not got.blocks:
        notes = "\n".join(f"- {n}" for n in got.notes)
        found = prompts.NO_FEEDBACK_FOUND.format(candidate=candidate)
        return _said(f"{found}\n{notes}\n\n{prompts.NEED_INPUT}".replace("\n\n\n", "\n\n"))
    return _shown(await build(deps, got.blocks, turn.req, candidate, got.notes,
                              any_source_failed=got.any_source_failed), candidate)


def _said(reply: str) -> dict:
    return {"reply": reply, "route": "", "pause": {}, "data": {}}


def _shown(outcome, candidate: str) -> dict:
    return {"reply": outcome.reply, "data": outcome.data, "report": outcome.report, "candidate": candidate,
            "route": "", "pause": {}}
