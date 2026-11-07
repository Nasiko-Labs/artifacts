"""The alert-tuner LangGraph.

    START → turn ─┬─ off topic / no trace context → fixed reply ───────────────→ END
                  ├─ "show incidents for X" → from the stored report (code) ───→ END
                  ├─ pasted metric values → proposals recomputed (code) ───────→ END
                  ├─ "open the PR for #1 [in owner/repo]" → prepare → plans ───→ END (walk outside)
                  │                                     └─ no repo → question → END (pause)
                  └─ otherwise → analysis (incidents → logs → noise → proposals) → END

A message that is only a greeting or small talk never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId (thread_id): the last report, its parameters and the repository
named, so follow-ups work from them. The graph is compiled per turn because its node closes over that
turn's dependencies (LLM client, gateway, inbound traceparent); the checkpointer is shared. Plans leave
the graph in the turn result; the approve -> write walk runs outside it, its state on the A2A task.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import analysis
import asks
import intake
import pasted
import prepare as preparing
import prompts
import render
import smalltalk
from deps import TurnDeps
from nasiko_hitl import question_metadata
from noise import Params
from plan import Plan

__all__ = ["AlertTunerAgent", "TurnResult"]

REPO_PAUSE_KEY = "alert_tuner_repo"
CANCEL = "Cancel"


class TunerState(TypedDict, total=False):
    query: str
    reply: str
    report: dict | None       # the last analysis: what "#1", "show incidents for X" and pasted values read
    repo: str                 # the repository the user named last
    envelope: dict | None     # this turn
    plans: list[dict]         # this turn: pull-request plans for the approval walk
    pause: dict | None        # this turn: the repository question


@dataclass(frozen=True)
class TurnResult:
    reply: str
    plans: tuple[Plan, ...] = ()
    envelope: dict | None = None
    pause: dict | None = None     # {"text", "hitl", "metadata"}


class AlertTunerAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "reply": "", "envelope": None, "plans": [], "pause": None}
        # One checkpoint per turn: a crash mid-turn resumes from the previous turn, not mid tool call.
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        return _result(final)

    async def resume_repo(self, answer: str, pause: Mapping[str, Any], context_id: str, deps: TurnDeps) -> TurnResult:
        """The answer to "Which repo holds the alert definitions?"; asked once, never again."""
        repo = asks.repo_in(answer)
        if not repo:
            return TurnResult("No pull request opened: I need the repository as `owner/repo`. Ask again with it, "
                              'e.g. "open the PR for #1 in acme/infra".')
        values = await self._values(context_id)
        if not values.get("report"):
            return TurnResult(prompts.NO_REPORT)
        numbers = tuple(int(n) for n in pause.get("numbers") or (1,))
        update = await _pr(deps, values["report"], repo, numbers, str(pause.get("hint") or ""), bool(pause.get("capped")))
        await self._save(context_id, {**update, "repo": repo})
        return _result(update)

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)

    async def _save(self, context_id: str, values: Mapping[str, Any]) -> None:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), {**values, "plans": [], "pause": None}, as_node="turn")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _result(state: Mapping[str, Any]) -> TurnResult:
    plans = tuple(Plan.from_dict(p) for p in state.get("plans") or ())
    return TurnResult(state.get("reply") or "", plans, state.get("envelope") or None, state.get("pause") or None)


def _build_graph(deps: TurnDeps | None) -> StateGraph:
    async def turn(state: TunerState) -> dict:
        return await _turn(state, deps)

    graph = StateGraph(TunerState)
    graph.add_node("turn", turn)
    graph.add_edge(START, "turn")
    graph.add_edge("turn", END)
    return graph


async def _turn(state: TunerState, deps: TurnDeps) -> dict:
    query = state["query"]
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if not await intake.in_scope(deps, query):
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    report = state.get("report")
    previous = Params(**report["params"]) if report else None
    ask = asks.read(query, previous, state.get("repo") or "")
    if ask.kind == "show":
        if not report:
            return {"reply": prompts.NO_REPORT}
        return {"reply": analysis.show(report, ask.show), "envelope": render.envelope(report)}
    if ask.kind == "pr":
        if not report:
            return {"reply": prompts.NO_REPORT}
        if not ask.repo:
            return {"pause": _repo_question(ask)}
        return {**await _pr(deps, report, ask.repo, ask.numbers, ask.path_hint, ask.capped), "repo": ask.repo}
    values = pasted.metric_values(query)
    if report and values and not asks.changes_params(query) and pasted.export(query) is None:
        return _with_values(report, values)
    done = await analysis.analyze(deps, ask, query)
    update: dict = {"reply": done.reply}
    if done.report is not None:
        stored = {**done.report, "repo": ask.repo}
        update = {**update, "report": stored, "envelope": render.envelope(stored)}
    return {**update, "repo": ask.repo} if ask.repo else update


def _with_values(report: Mapping, values: Mapping[str, Sequence[float]]) -> dict:
    updated = analysis.with_values(report, values)
    unmatched = analysis.unmatched_values(report, values)
    note = (f"\n\nNo noisy alert matches {', '.join(render.code(n) for n in unmatched)}, so those values were not "
            "used.") if unmatched else ""
    return {"reply": render.markdown(updated) + note, "report": updated, "envelope": render.envelope(updated)}


def _repo_question(ask: asks.Ask) -> dict:
    hitl = question_metadata(prompts.ASK_REPO_HEADER, [(CANCEL, "Don't open a pull request.")],
                             allow_custom_input=True)
    metadata = {REPO_PAUSE_KEY: {"numbers": list(ask.numbers), "hint": ask.path_hint, "capped": ask.capped}}
    return {"text": prompts.ASK_REPO_TEXT, "hitl": hitl, "metadata": metadata}


async def _pr(deps: TurnDeps, report: Mapping, repo: str, numbers: Sequence[int], hint: str, capped: bool) -> dict:
    ready = await preparing.prepare(deps, report, numbers, repo, hint)
    if ready.reply:
        return {"reply": ready.reply}
    marked = _mark(report, ready)
    notes = list(ready.notes)
    if capped:
        notes.insert(0, f"I take at most {asks.MAX_PR_ALERTS} proposals per request; these are the first ones.")
    if ready.copyable:
        notes.append(ready.copyable)
    reply = "\n\n".join(notes)
    if not ready.plans:
        return {"reply": reply or "There is nothing I can open a pull request for.", "report": marked}
    return {"reply": reply, "report": marked, "plans": [p.to_dict() for p in ready.plans]}


def _mark(report: Mapping, ready: preparing.Prepared) -> dict:
    """The stored proposals with the located definition (file, line, diff) or why there is none."""
    located = {p.alert: p for p in ready.plans}
    missing = {alert: (why, ambiguous) for alert, why, ambiguous in ready.def_notes}
    proposals = []
    for prop in report.get("proposals") or ():
        if prop["kind"] == "lengthen_for" and prop["alert"] in located:
            p = located[prop["alert"]].patch
            prop = {**prop, "file": p.path, "line": p.line, "patch": p.diff}
        elif prop["kind"] == "lengthen_for" and prop["alert"] in missing:
            why, ambiguous = missing[prop["alert"]]
            prop = {**prop, "note": why, "ambiguous": ambiguous}
        proposals.append(prop)
    return {**report, "proposals": proposals}
