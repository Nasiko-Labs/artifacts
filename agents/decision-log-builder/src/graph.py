"""The decision-log LangGraph: one turn's read side.

    START → intent ─┬─ off-topic → END (fixed decline)
                    ├─ follow-up on the list (log 1,3 / log all new / other doc / text only) → END
                    └─ collect ─┬─ envelope: `decisions` from a previous step
                                ├─ paste: chat lines pasted in the message
                                └─ scan (chat tool loop) → judge (LLM per channel) → verify (code)
                       collect → END

A greeting or small talk never enters the graph (smalltalk.py). State is checkpointed per A2A contextId,
so the list, the chosen log doc and what was written survive between turns and replicas. The graph is
compiled per turn because its nodes close over that turn's dependencies; the checkpointer is shared. The
document walk (walk.py) runs outside the graph, its state on the A2A task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import answers
import judge
import parse
import prompts
import render
import smalltalk
import walk
from deps import TurnDeps
from discovery import discover
from llmcall import ask_once
from models import Decision, Doc
from paste import read_paste
from scan import scan

log = logging.getLogger("decision_log.graph")

_ROUTES = (prompts.SCAN, prompts.OFF_TOPIC, parse.LOG_ALL, parse.CHANGE_DOC, parse.SHOW_ONLY)


class LogState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    command: str            # this turn: the follow-up asked for, if any
    decisions: list[dict]   # the list last shown, numbered: what "log 2" refers to
    report: dict            # window, drops, coverage, pasted / envelope input
    doc: dict | None        # the log document chosen in this conversation
    log_text: str           # its text as last read (for duplicates)
    written: list[str]      # decision ids appended in this conversation
    write_blocked: bool     # the gateway blocked the write tool


class _Scope:
    def __init__(self, deps: TurnDeps, ask: parse.Ask):
        self.deps = deps
        self.ask = ask


class DecisionLogAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, text: str, metadata: Mapping[str, Any], context_id: str, deps: TurnDeps) -> walk.Outcome:
        canned = smalltalk.reply_to(text)
        if canned is not None:  # greeting or small talk: no tool call, no LLM call, state untouched
            return walk.Outcome("complete", canned)
        if not deps.traceparent:
            return walk.Outcome("complete", prompts.NO_TRACE_CONTEXT)
        if answers.decision(text)[0] == "yes":  # "Approve" with nothing paused: never a write
            return walk.Outcome("complete", walk.NOT_WAITING)
        ask = parse.read(text, deps.today, metadata)
        if ask.edits_history:
            return walk.Outcome("complete", prompts.APPEND_ONLY)
        graph = _build_graph(_Scope(deps, ask)).compile(checkpointer=self._checkpointer)
        fresh = {"query": text, "reply": "", "route": "", "command": ""}
        final = await graph.ainvoke(fresh, _config(context_id), durability="exit")
        if final.get("reply"):
            return walk.Outcome("complete", final["reply"])
        return await walk.begin(deps, _setup(final, ask))

    async def remember(self, context_id: str, values: Mapping[str, Any]) -> None:
        """Store the conversation's list, doc and written ids for later turns."""
        if not values:
            return
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), dict(values), as_node="collect")


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _setup(state: Mapping[str, Any], ask: parse.Ask) -> walk.Setup:
    command = state.get("command") or ""
    doc = Doc.from_dict(state.get("doc"))
    hint = ask.doc_title or ""
    change_doc = command == parse.CHANGE_DOC or bool(hint and doc and doc.title.casefold() != hint.casefold())
    rescanned = not command
    if rescanned and doc is not None and doc.state in ("found", "created") and not change_doc:
        change_doc, hint = True, doc.title  # a fresh list: read the log again, so duplicates are current
    base = walk.Walk(decisions=tuple(Decision.from_dict(d) for d in state.get("decisions") or ()),
                     report=dict(state.get("report") or {}), doc=doc, log_text=str(state.get("log_text") or ""),
                     written=tuple(state.get("written") or ()), write_blocked=bool(state.get("write_blocked")),
                     title_hint=hint, json_mode=ask.wants_json)
    ids = parse.numbers_of(command) if command.startswith(parse.LOG_PREFIX) else ()
    return walk.Setup(walk=base, show_only=ask.show_only or command == parse.SHOW_ONLY, change_doc=change_doc,
                      ids=ids, every=command == parse.LOG_ALL)


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def intent(state: LogState) -> dict:
        return await _intent(state, scope)

    async def collect(state: LogState) -> dict:
        return await _collect(scope)

    graph = StateGraph(LogState)
    graph.add_node("intent", intent)
    graph.add_node("collect", collect)
    graph.add_edge(START, "intent")
    graph.add_conditional_edges("intent", _route, ["collect", END])
    graph.add_edge("collect", END)
    return graph


def _route(state: LogState) -> str:
    return state.get("route") or END


async def _intent(state: LogState, scope: _Scope) -> dict:
    ask = scope.ask
    decisions = [Decision.from_dict(d) for d in state.get("decisions") or ()]
    command = parse.command(ask.text, bool(decisions))
    if command:
        return {"command": command, "route": ""}
    if ask.envelope is not None or read_paste(ask.text, scope.deps.today) is not None:
        return {"route": "collect"}
    prior = "\n".join(render.decision_line(d) for d in decisions)
    answer = (await ask_once(scope.deps, ({"role": "system", "content": prompts.intent_system(prior)},
                                          {"role": "user", "content": prompts.strip_markers(ask.text)}))).strip()
    routed = _routed(answer)
    if routed == prompts.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if routed in (prompts.SCAN, None) or (not decisions and routed in (parse.CHANGE_DOC, parse.SHOW_ONLY)):
        return {"route": "collect"}
    if not decisions:
        return {"reply": prompts.NO_LIST_YET, "route": ""}
    return {"command": routed, "route": ""}


def _routed(answer: str) -> str | None:
    word = answer.strip().strip("`'\". ").upper()
    if word.startswith(parse.LOG_PREFIX):
        numbers = parse.numbers_of(word)
        return parse.LOG_PREFIX + ",".join(numbers) if numbers else None
    return next((r for r in _ROUTES if word.startswith(r)), None)


async def _collect(scope: _Scope) -> dict:
    ask = scope.ask
    report = {"window_days": ask.window_days, "today": scope.deps.today.isoformat(), "coverage": [],
              "reviewed": False}
    if ask.envelope is not None:
        return _from_envelope(ask.envelope, report)
    pasted = read_paste(ask.text, scope.deps.today)
    if pasted is not None:
        found = await judge.judge(scope.deps, pasted, ask.text)
        return _listed(found, {**report, "pasted": True})
    catalog, failure = await discover(scope.deps, "scan")
    if failure is not None:
        return {"reply": failure, "route": ""}
    outcome = await scan(scope.deps, catalog, ask.window_days, ask.channels)
    if outcome.reply is not None:
        return {"reply": outcome.reply, "route": ""}
    found = await judge.judge(scope.deps, outcome.messages, outcome.seen_text)
    return _listed(found, {**report, "coverage": list(outcome.notes), "unbacked": outcome.dropped})


def _listed(found: judge.Judged, report: dict) -> dict:
    report = {**report, "dropped": int(report.get("unbacked") or 0) + found.dropped,
              "not_decided": found.not_decided,
              "coverage": list(dict.fromkeys([*report.get("coverage", ()), *found.notes]))}
    if not found.decisions:
        nothing = (prompts.NOTHING_IN_PASTE if report.get("pasted") else prompts.nothing_found(report["window_days"]))
        return {"reply": "\n\n".join([nothing, *report["coverage"]]), "route": ""}
    log.info("listing %d decision(s)", len(found.decisions))
    return {"decisions": [d.to_dict() for d in found.decisions], "report": report, "written": [], "route": ""}


def _from_envelope(envelope: Mapping[str, Any], report: dict) -> dict:
    raw = [Decision.from_dict(d) for d in envelope.get("decisions") or () if isinstance(d, Mapping)]
    kept = [d for d in raw if d.decision and d.quote]
    if not kept:
        return {"reply": "The envelope has no decisions with a decision text and a quote, so there is nothing "
                         "to log.", "route": ""}
    numbered = [Decision.from_dict({**d.to_dict(), "id": str(n)}) for n, d in enumerate(kept, start=1)]
    return {"decisions": [d.to_dict() for d in numbered], "written": [], "route": "",
            "report": {**report, "from_envelope": True, "dropped": len(raw) - len(kept)}}
