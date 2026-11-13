"""The meeting-notes LangGraph, stateless.

    START -> intake ─┬─ process (parse turns, attendees, map/reduce, checks) -> END
                     ├─ ask (one quote-checked question)                     -> END
                     └─ fixed reply ─────────────────────────────────────────> END

There is no checkpointer: the graph's state lives for one turn and is built from that turn's input alone.
Earlier turns reach this agent only through the platform's history block, which `intake` reads (via
`recall`). Nothing pauses: every turn ends with a reply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import chunking
import envelope
import followup
import intake
import process
import prompts
import render
import smalltalk
import transcript
from attendees import attendees
from inputs import Incoming, title_in, word_count
from items import UNKNOWN, Result, Turn
from llm import TurnDeps

ASK_NAME = intake.ASK_NAME
FIRST_PART_NOTE = "That meeting is long, so I answered from its first part only."


class MeetingState(TypedDict, total=False):
    route: str
    reply: str
    data: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    data: dict = field(default_factory=dict)


class _Scope:
    """One turn's input and dependencies (never persisted)."""

    def __init__(self, deps: TurnDeps, incoming: Incoming):
        self.deps, self.incoming = deps, incoming
        self.plan: intake.Plan | None = None


class MeetingNotesAgent:
    async def turn(self, incoming: Incoming, deps: TurnDeps) -> TurnResult:
        graph = _build_graph(_Scope(deps, incoming)).compile()
        final = await graph.ainvoke({"route": "", "reply": "", "data": None})
        return TurnResult(final.get("reply") or "", final.get("data") or {})


def _build_graph(scope: _Scope) -> StateGraph:
    async def intake_node(_state: MeetingState) -> dict:
        return _intake(scope)

    async def process_node(_state: MeetingState) -> dict:
        return await _process(scope)

    async def ask_node(_state: MeetingState) -> dict:
        return await _ask(scope)

    graph = StateGraph(MeetingState)
    graph.add_node("intake", intake_node)
    graph.add_node("process", process_node)
    graph.add_node("ask", ask_node)
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", lambda s: s.get("route") or END, ["process", "ask", END])
    graph.add_edge("process", END)
    graph.add_edge("ask", END)
    return graph


def _intake(scope: _Scope) -> dict:
    plan = intake.plan_turn(scope.incoming)
    scope.plan = plan
    if plan.route == "reply":
        return {"route": "", "reply": _with_notes(plan.reply, plan.notes)}
    return {"route": plan.route}


def _present(result: Result, meeting: envelope.Meeting, view: str, wants_json: bool,
             notes: tuple[str, ...]) -> tuple[str, dict]:
    data = envelope.build(result, meeting, render.notes_markdown(result, meeting))
    text = render.render_json(data) if wants_json else render.render(result, meeting, view, notes)
    return text, data


async def _process(scope: _Scope) -> dict:
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
    if fixed is not None:
        return {"reply": _with_notes(fixed, plan.notes)}
    if outcome.result.is_empty:
        return {"reply": _with_notes(prompts.NOTHING_BACKED, plan.notes)}
    meeting = envelope.Meeting(parsed.turns, people, parsed.has_speakers, parsed.title or title_in(plan.instruction),
                               parsed.date, word_count(plan.source), plan.my_name)
    reply, data = _present(outcome.result, meeting, plan.view, plan.wants_json, plan.notes)
    return {"reply": reply, "data": data}


async def _ask(scope: _Scope) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    notes = plan.notes
    if plan.material == "notes":
        turns, name, cite = reply_lines(plan.source), "NOTES", False
    else:
        turns, name, cite = transcript.parse(plan.source).turns if plan.source else (), "TRANSCRIPT", True
        chunks = chunking.split(turns) if turns else ((),)
        if len(chunks) > 1:
            turns, notes = chunks[0], (FIRST_PART_NOTE, *notes)
    answer = await followup.ask(scope.deps, scope.incoming.text, turns, name, scope.incoming.context, cite)
    return {"reply": _with_notes(answer, notes)}


def reply_lines(reply: str) -> tuple[Turn, ...]:
    """An earlier notes reply as quotable lines (L1..Ln), so an answer is still checked against real text."""
    lines = [line.strip() for line in (reply or "").splitlines() if line.strip()]
    return tuple(Turn(id=f"L{n}", speaker=UNKNOWN, time="", text=line) for n, line in enumerate(lines, start=1))


def _with_notes(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(part for part in (text, *notes) if part)
