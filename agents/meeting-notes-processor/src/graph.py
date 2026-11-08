"""The meeting-notes LangGraph.

    START -> intake ─┬─ process (parse turns, attendees, map/reduce, checks) -> END
                     ├─ followup (view, "I'm Raj", or one question)         -> END
                     └─ fixed reply / pause ─────────────────────────────────> END

State is checkpointed per A2A contextId (thread_id): the parsed turns, the checked result and the user's
name, so follow-ups work from the stored meeting. The graph is compiled per turn because its nodes close
over that turn's input and dependencies; the checkpointer is shared. A pause leaves the graph as a `pause`
value; its state lives on the A2A task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import envelope
import followup
import hitl
import intake
import process
import prompts
import render
import smalltalk
import transcript
from attendees import attendees
from inputs import Incoming, title_in, word_count
from items import Result, turns_from_state, turns_to_state
from llm import TurnDeps

ASK_NAME = 'Tell me who you are first (for example "I\'m Raj"), then ask for only mine.'


class MeetingState(TypedDict, total=False):
    turns: list[dict]
    meta: dict           # title, date, has_speakers, words
    result: dict         # Result.to_dict()
    chunked: bool
    my_name: str
    route: str           # this turn only
    reply: str           # this turn only
    data: dict | None    # this turn only: the envelope
    pause: dict | None   # this turn only: hitl.Pending metadata


@dataclass(frozen=True)
class TurnResult:
    reply: str
    pause: hitl.Pending | None = None
    data: dict = field(default_factory=dict)


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming, forced: intake.Plan | None = None):
        self.deps, self.incoming, self.forced = deps, incoming, forced
        self.plan: intake.Plan | None = None


class MeetingNotesAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        return await self._run(_Scope(deps, incoming), context_id)

    async def resume(self, pending: hitl.Pending, incoming: Incoming, context_id: str, deps: TurnDeps) -> TurnResult:
        plan = intake.plan_resume(pending, hitl.interpret(pending, incoming.text))
        return await self._run(_Scope(deps, incoming, forced=plan), context_id)

    async def _run(self, scope: _Scope, context_id: str) -> TurnResult:
        graph = _build_graph(scope).compile(checkpointer=self._checkpointer)
        fresh = {"route": "", "reply": "", "pause": None, "data": None}
        final = await graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}}, durability="exit")
        return TurnResult(final.get("reply") or "", hitl.Pending.from_metadata(final.get("pause")),
                          final.get("data") or {})


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(state: MeetingState) -> dict:
        return _intake(state, scope)

    async def process_node(state: MeetingState) -> dict:
        return await _process(state, scope)

    async def followup_node(state: MeetingState) -> dict:
        return await _followup(state, scope)

    graph = StateGraph(MeetingState)
    graph.add_node("intake", intake_node)
    graph.add_node("process", process_node)
    graph.add_node("followup", followup_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["process", "followup", END])
    graph.add_edge("process", END)
    graph.add_edge("followup", END)
    return graph


def _intake(state: MeetingState, scope: _Scope) -> dict:
    plan = scope.forced or intake.plan_turn(scope.incoming, bool(state.get("result")))
    scope.plan = plan
    if plan.route == "pause":
        return {"route": "", "pause": plan.pending.to_metadata()}
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    if plan.route == "set_name" and not state.get("result"):
        reply = f"Noted: you're {plan.my_name}. Paste a meeting transcript and I'll mark your items."
        return {"route": "", "my_name": plan.my_name, "reply": reply}
    return {"route": "process" if plan.route == "process" else "followup"}


def _meeting(state: MeetingState, my_name: str = "") -> envelope.Meeting:
    turns = turns_from_state(state.get("turns"))
    meta = state.get("meta") or {}
    return envelope.Meeting(turns, attendees(turns), bool(meta.get("has_speakers")), str(meta.get("title") or ""),
                            str(meta.get("date") or ""), int(meta.get("words") or 0), my_name or state.get("my_name") or "")


def _present(result: Result, meeting: envelope.Meeting, view: str, wants_json: bool,
             notes: tuple[str, ...]) -> tuple[str, dict]:
    data = envelope.build(result, meeting, render.notes_markdown(result, meeting))
    text = render.render_json(data) if wants_json else render.render(result, meeting, view, notes)
    return text, data


async def _process(state: MeetingState, scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    parsed = transcript.parse(plan.source)
    if not parsed.turns:
        return {"reply": _with_notes(prompts.NO_TURNS, plan.notes)}
    people = attendees(parsed.turns)
    outcome = await process.run(scope.deps, parsed.turns, [a.name for a in people], plan.instruction,
                                scope.incoming.context)
    fixed = {"off_topic": smalltalk.OFF_TOPIC_REPLY, "not_meeting": prompts.NOT_MEETING,
             "failed": prompts.MODEL_FAILED}.get(outcome.status)
    if fixed is not None:  # the stored meeting, if any, is kept
        return {"reply": _with_notes(fixed, plan.notes)}
    if outcome.result.is_empty:
        return {"reply": _with_notes(prompts.NOTHING_BACKED, plan.notes)}
    my_name = plan.my_name or state.get("my_name") or ""
    meeting = envelope.Meeting(parsed.turns, people, parsed.has_speakers, parsed.title or title_in(plan.instruction),
                               parsed.date, word_count(plan.source), my_name)
    reply, data = _present(outcome.result, meeting, "default", plan.wants_json, plan.notes)
    meta = {"title": meeting.title, "date": parsed.date, "has_speakers": parsed.has_speakers,
            "words": meeting.words}
    return {"reply": reply, "data": data, "turns": turns_to_state(parsed.turns), "meta": meta,
            "result": outcome.result.to_dict(), "chunked": outcome.chunked, "my_name": my_name}


async def _followup(state: MeetingState, scope: _Scope) -> dict:
    plan = scope.plan
    result = Result.from_dict(state.get("result"))
    if plan.route == "set_name":
        reply, data = _present(result, _meeting(state, plan.my_name), "default", False, plan.notes)
        return {"reply": reply, "data": data, "my_name": plan.my_name}
    if plan.route == "rerender":
        meeting = _meeting(state)
        if plan.view == "mine" and not meeting.my_name:
            return {"reply": ASK_NAME}
        reply, data = _present(result, meeting, plan.view, plan.wants_json, plan.notes)
        return {"reply": reply, "data": data}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    answer = await followup.ask(scope.deps, scope.incoming.text, turns_from_state(state.get("turns")),
                                result if state.get("result") else None, bool(state.get("chunked")),
                                scope.incoming.context)
    return {"reply": _with_notes(answer, plan.notes)}


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
