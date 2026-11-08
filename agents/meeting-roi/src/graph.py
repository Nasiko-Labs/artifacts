"""The meeting-roi LangGraph.

    START ─┬─ (asks me to cancel / edit / send) ───────────→ refuse → END
           ├─ (a report was given in this context) → followup ─┬─ answered ──→ END
           │                                                    ├─ DRAFT_NOTE → draft → END
           │                                                    └─ NEEDS_PULL ─┐
           └─ (no report yet) ───────────────────────────────────→ audit ←─────┘ → END

Before the graph, in code: a greeting gets the canned intro (zero calls), and a request without a
trace context gets a fixed reply. `audit` runs discover → calendar → group → notes → chat → decisions
→ compute → render (audit.py); `draft` renders the note from a template.

State is checkpointed per A2A contextId (thread_id): the last report and its meetings, numbered as
shown, so a follow-up is answered from them. The graph is compiled per turn because its nodes close
over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import drafts
import prompts
import render
import smalltalk
from audit import run_audit
from deps import TurnDeps
from records import MeetingRoi
from request import parse
from steps import ask_once

__all__ = ["MeetingRoiAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("meeting_roi.graph")

_LINK = re.compile(r"https?://[^\s)>\]]+")
_RERUN = re.compile(r"\b(?:audit|re-?run|refresh|again|last \d+|past \d+)\b", re.IGNORECASE)


class RoiState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    report: str            # the last report shown in this context: what follow-ups are answered from
    meetings: list[dict]   # its meetings, in the order shown (what "#2" refers to)
    window_days: int
    focus: str             # the meeting last discussed
    envelope: dict | None  # this turn only: the envelope of a fresh report
    draft_n: int


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict[str, Any] | None = None


class MeetingRoiAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:
            return TurnResult(canned)
        if not deps.traceparent:
            return TurnResult(prompts.NO_TRACE_CONTEXT)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        config = {"configurable": {"thread_id": context_id}}
        fresh = {"query": query, "reply": "", "route": "", "envelope": None, "draft_n": 0}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, config, durability="exit")
        return TurnResult(final.get("reply") or prompts.UNREADABLE_ANSWER, final.get("envelope"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: RoiState) -> dict:
        return await _followup(state, deps)

    async def audit(state: RoiState) -> dict:
        return await _audit(state, deps)

    graph = StateGraph(RoiState)
    graph.add_node("refuse", _refuse)
    graph.add_node("followup", followup)
    graph.add_node("draft", _draft)
    graph.add_node("audit", audit)
    graph.add_conditional_edges(START, _entry, ["refuse", "followup", "audit"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["audit", "draft", END])
    for node in ("refuse", "draft", "audit"):
        graph.add_edge(node, END)
    return graph


def _entry(state: RoiState) -> str:
    if drafts.is_write_request(state["query"]):
        return "refuse"
    return "followup" if state.get("meetings") else "audit"


def _meetings(state: RoiState) -> tuple[MeetingRoi, ...]:
    return tuple(MeetingRoi.from_dict(m) for m in state.get("meetings") or ())


def _refuse(state: RoiState) -> dict:
    """"Cancel it": never a write. The fixed note, plus the copyable draft when there is a report."""
    meetings = _meetings(state)
    target = drafts.pick(meetings, state["query"], state.get("focus"))
    if target is None:
        return {"reply": f"{prompts.NO_CANCEL_NOTE} Say \"audit my recurring meetings\" and I'll propose changes "
                         "you can make yourself.", "route": ""}
    note = drafts.note(target, state.get("window_days") or 28)
    return {"reply": f"{prompts.NO_CANCEL_NOTE} Here is the proposal instead.\n\n{note}", "route": "",
            "focus": target.series_title}


def _draft(state: RoiState) -> dict:
    meetings = _meetings(state)
    n = state.get("draft_n") or 0
    target = meetings[n - 1] if 0 < n <= len(meetings) else drafts.pick(meetings, state["query"], state.get("focus"))
    if target is None:
        return {"reply": prompts.NO_PRIOR, "route": ""}
    return {"reply": drafts.note(target, state.get("window_days") or 28), "route": "",
            "focus": target.series_title}


async def _followup(state: RoiState, deps: TurnDeps) -> dict:
    query = state["query"]
    if drafts.is_draft_request(query):
        return {"route": "draft"}
    if _RERUN.search(query) or parse(query).pasted:
        return {"route": "audit"}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(state.get('report') or '')}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    if text.startswith(prompts.NEEDS_PULL):
        return {"route": "audit"}
    if text.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if text.startswith(prompts.DRAFT_NOTE):
        digits = re.findall(r"\d+", text)
        return {"route": "draft", "draft_n": int(digits[0]) if digits else 0}
    if not text:
        return {"reply": prompts.UNREADABLE_ANSWER, "route": ""}
    target = drafts.pick(_meetings(state), query)
    return {"reply": _known_links_only(text, state.get("report") or ""), "route": "",
            "focus": target.series_title if target else state.get("focus", "")}


def _known_links_only(text: str, report: str) -> str:
    return _LINK.sub(lambda m: m.group(0) if m.group(0) in report else "[link removed]", text)


async def _audit(state: RoiState, deps: TurnDeps) -> dict:
    req = parse(state["query"])
    outcome = await run_audit(deps, req)
    if outcome.report is None:  # a fixed reply keeps the previous report
        return {"reply": outcome.reply, "route": ""}
    text = render.report(outcome.report, req.prior_meetings)
    meetings = render.ordered(outcome.report.meetings)
    log.info("audited %d series", len(meetings))
    return {"reply": text, "route": "", "report": text, "meetings": [m.to_dict() for m in meetings],
            "window_days": req.window_days, "envelope": render.envelope(outcome.report, text), "focus": ""}
