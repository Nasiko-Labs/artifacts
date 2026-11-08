"""The dashboard-trust-checker LangGraph.

    START -+- (an audit in this context, no new source) -> followup -+- WHY / RETIRE / THRESHOLD / DELETE -> END
           |                                                         +- NEW_RUN -> build
           +- (no audit yet, a new sheet, a pasted table, a pick) -----> build -> END

build = pasted table or envelope, else (scope check when the message names nothing of this job) discover ->
locate/read the sheet (tool loop, post-checked) -> map headers (code; one model call only for headers code
does not know; a single-select when two columns claim one field) -> classify (code) -> chat complaints
(code-driven search, capped) -> render (code).
A greeting never enters the graph (smalltalk.py). The classification itself never calls a model.

State is checkpointed per A2A contextId, so a follow-up ("why is X flagged?", "stale after 14 days") is
answered from the stored table and complaints with no tool or model call. The graph is compiled per turn
because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import classify
import complaints
import coverage
import headers
import models
import parse
import paste
import prompts
import render
import sheet
import smalltalk
from deps import TurnDeps
from discovery import discover
from headers import HeaderMap
from llm import ask_once
from nasiko_hitl import question_metadata
from table import Table

__all__ = ["COLUMN_KEY", "SHEET_KEY", "TrustAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("trust_checker.graph")

TURN_DEADLINE_SECS = 240
SHEET_KEY = "dtc.sheet_pick"
COLUMN_KEY = "dtc.column_pick"
MAX_OPTIONS = 10
OUT_OF_TIME = "I ran out of time before finishing this audit. Try again, or paste the table."
SHEET_HEADER = "Which spreadsheet is the dashboard inventory?"


class TrustState(TypedDict, total=False):
    query: str
    request: dict
    pick: dict | None          # this turn: {"title"} (sheet pick) or {"field", "column"} (column pick)
    table: dict                # table.Table of the last audit
    header_map: dict           # headers.HeaderMap of the last audit
    limits: dict               # {"stale_days", "unused_days"} of the last audit
    scan: dict                 # complaints.Scan of the last audit
    pending: dict | None       # a table waiting on a column pick: {"table", "header_map", "request"}
    reply: str                 # this turn
    route: str
    question: dict | None      # this turn: {"text", "hitl", "metadata"}
    envelope: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    question: dict | None = None
    envelope: dict | None = None


class TrustAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps, metadata: Mapping[str, Any] | None = None,
                   pick: Mapping[str, str] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query) if pick is None else None
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "request": parse.parse(query, metadata).to_dict(), "pick": dict(pick or {}) or None,
                 "reply": "", "route": "", "question": None, "envelope": None}
        try:
            final = await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"),
                                           TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        return TurnResult(final.get("reply") or "", final.get("question"), final.get("envelope"))


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: TrustState) -> dict:
        return await _followup(state, deps)

    async def build_node(state: TrustState) -> dict:
        return await _build(state, deps)

    graph = StateGraph(TrustState)
    graph.add_node("followup", followup)
    graph.add_node("build", build_node)
    graph.add_conditional_edges(START, _entry, ["followup", "build"])
    graph.add_conditional_edges("followup", _route, ["build", END])
    graph.add_edge("build", END)
    return graph


def _entry(state: TrustState) -> str:
    request = _request(state)
    if state.get("pick") or not state.get("table") or request.sheet or request.rerun:
        return "build"
    return "build" if paste.read(state["query"]) is not None else "followup"


def _route(state: TrustState) -> str:
    return state.get("route") or END


def _request(state: Mapping[str, Any]) -> parse.Request:
    return parse.Request.from_dict(state.get("request") or {})


# --- build: read the inventory and audit it ------------------------------------------------------------------

async def _build(state: TrustState, deps: TurnDeps) -> dict:
    query, request, pick = state["query"], _request(state), state.get("pick") or {}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    if pick.get("column"):
        return await _column_picked(state, deps, pick)
    pasted = paste.read(query)
    if pasted is not None:
        return await _audit(state, deps, pasted, request, None)
    if request.delete:
        return {"reply": render.delete_declined(None, deps.now.date())}
    if not pick.get("title") and not request.sheet and not parse.mentions_domain(query) and await _off_topic(deps, query):
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    catalog, failure = await discover(deps, ("read",))
    if failure is not None:
        return {"reply": failure}
    reader = sheet.reader_for(deps, catalog)
    outcome = await sheet.read(deps, reader, query, request, pick.get("title"))
    if outcome.off_topic:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if outcome.candidates:
        return {"question": _sheet_question(outcome.candidates, query)}
    if outcome.table is None:
        return {"reply": outcome.reply or prompts.SHEET_UNREADABLE}
    return await _audit(state, deps, outcome.table, request, reader)


async def _off_topic(deps: TurnDeps, query: str) -> bool:
    answer = await ask_once(deps, ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                                   {"role": "user", "content": prompts.fence_data(query)}))
    return not answer.strip().upper().startswith(prompts.IN_SCOPE)


def _sheet_question(candidates: tuple[str, ...], query: str) -> dict:
    hitl = question_metadata(SHEET_HEADER, [(title, "") for title in candidates[:MAX_OPTIONS]],
                             multi_select=False, allow_custom_input=False)
    return {"text": "I found more than one spreadsheet that could be it. " + SHEET_HEADER, "hitl": hitl,
            "metadata": {SHEET_KEY: {"names": list(candidates[:MAX_OPTIONS]), "query": query}}}


async def _audit(state: TrustState, deps: TurnDeps, table: Table, request: parse.Request,
                 reader: sheet.Reader | None) -> dict:
    header_map = headers.map_headers(table.header)
    if header_map.needs_model():
        header_map = await _model_map(deps, header_map)
    if header_map.ambiguous:
        return _column_question(table, header_map, request)
    return await _finish(state, deps, table, header_map, request, reader)


async def _model_map(deps: TurnDeps, current: HeaderMap) -> HeaderMap:
    unknown = [current.header[i] for i in current.unmapped]
    answer = await ask_once(deps, ({"role": "system", "content": prompts.HEADER_MAP_SYSTEM},
                                   {"role": "user", "content": prompts.fence_data(json.dumps(unknown))}))
    proposal = sheet.load_json_object(answer) or {}
    return headers.with_model(current, {str(k): str(v) for k, v in proposal.items()})


def _column_question(table: Table, header_map: HeaderMap, request: parse.Request) -> dict:
    target, candidates = next(iter(header_map.ambiguous.items()))
    names = [table.header[i] for i in candidates][:MAX_OPTIONS]
    text = prompts.HEADER_QUESTION.format(field=headers.label(target))
    hitl = question_metadata(text, [(n, "") for n in names], multi_select=False, allow_custom_input=False)
    pending = {"table": table.to_dict(), "header_map": header_map.to_dict(), "request": request.to_dict()}
    return {"pending": pending, "question": {"text": f"Two or more columns could be the {headers.label(target)}. "
                                                     + text, "hitl": hitl,
                                             "metadata": {COLUMN_KEY: {"field": target, "names": names}}}}


async def _column_picked(state: TrustState, deps: TurnDeps, pick: Mapping[str, str]) -> dict:
    pending = state.get("pending") or {}
    table = Table.from_dict(pending.get("table"))
    if table is None:
        return {"reply": prompts.HEADER_PICK_FAILED}
    resolved = headers.resolve(HeaderMap.from_dict(pending["header_map"]), str(pick.get("field")),
                               str(pick.get("column")))
    if resolved is None:
        return {"reply": prompts.HEADER_PICK_FAILED, "pending": None}
    request = parse.Request.from_dict(pending.get("request") or {})
    if resolved.ambiguous:
        return {**_column_question(table, resolved, request)}
    return {**await _finish(state, deps, table, resolved, request, None), "pending": None}


async def _finish(state: TrustState, deps: TurnDeps, table: Table, header_map: HeaderMap, request: parse.Request,
                  reader: sheet.Reader | None) -> dict:
    header_map = headers.with_name_fallback(header_map)
    if not header_map.can_judge():
        return {"reply": prompts.NOTHING_TO_JUDGE}
    limits = classify.Thresholds(request.stale_days, request.unused_days)
    dashboards = classify.classify(table, header_map, deps.now.date(), limits)
    if not dashboards:
        return {"reply": prompts.SHEET_UNREADABLE}
    reader = reader or await _chat_reader(deps)
    scan = await complaints.scan(deps, reader, dashboards)
    stored = {"table": table.to_dict(), "header_map": header_map.to_dict(),
              "limits": {"stale_days": limits.stale_days, "unused_days": limits.unused_days}, "scan": scan.to_dict()}
    return {**stored, **_rendered(stored, deps, request)}


async def _chat_reader(deps: TurnDeps) -> sheet.Reader | None:
    """A pasted table still gets complaint evidence when chat is connected."""
    catalog, failure = await discover(deps, ("read",))
    return None if failure is not None else sheet.reader_for(deps, catalog)


def _audit_of(stored: Mapping[str, Any], deps: TurnDeps) -> models.Audit:
    table = Table.from_dict(stored["table"])
    header_map = HeaderMap.from_dict(stored["header_map"])
    raw = stored.get("limits") or {}
    limits = classify.Thresholds(raw.get("stale_days"), raw.get("unused_days"))
    scan = complaints.Scan.from_dict(stored.get("scan"))
    dashboards = classify.apply_complaints(classify.classify(table, header_map, deps.now.date(), limits),
                                           scan.evidence)
    coverage_lines = () if scan.ok else (_chat_line(scan),)
    confidence = models.confidence_of(dashboards, scan.ok, scan.dropped)
    return models.Audit(dashboards, table.source, header_map.named(), coverage_lines, confidence, table.truncated)


def _chat_line(scan: complaints.Scan) -> str:
    return coverage.line(scan.status) + " No complaint evidence was added."


def _rendered(stored: Mapping[str, Any], deps: TurnDeps, request: parse.Request) -> dict:
    audit = _audit_of(stored, deps)
    envelope = models.envelope(audit)
    today = deps.now.date()
    if request.json_mode:
        reply = render.json_block(envelope)
    elif request.retire:
        reply = f"{render.report(audit, today)}\n\n{render.retire_list(audit, today)}"
    else:
        reply = render.report(audit, today)
    return {"reply": reply, "envelope": envelope}


# --- follow-ups --------------------------------------------------------------------------------------------

async def _followup(state: TrustState, deps: TurnDeps) -> dict:
    query, request = state["query"], _request(state)
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    today = deps.now.date()
    audit = _audit_of(state, deps)
    if request.delete:
        return {"reply": render.delete_declined(audit, today)}
    if request.stale_days or request.unused_days:
        return _threshold(state, deps, request.stale_days, request.unused_days, request)
    if request.json_mode:
        return {"reply": render.json_block(models.envelope(audit)), "envelope": models.envelope(audit)}
    if request.retire:
        return {"reply": render.retire_list(audit, today), "envelope": models.envelope(audit)}
    named = _named(audit, query)
    if named is not None:
        return {"reply": render.why(named, today)}
    answer = await ask_once(deps, ({"role": "system", "content": prompts.FOLLOWUP_SYSTEM},
                                   {"role": "user", "content": prompts.fence_data(query)}))
    return _sentinel(state, deps, audit, answer.strip())


def _threshold(state: TrustState, deps: TurnDeps, stale: int | None, unused: int | None,
               request: parse.Request) -> dict:
    """Recompute from the stored table and complaints: no tool call, no model call."""
    old = state.get("limits") or {}
    limits = {"stale_days": stale or old.get("stale_days"), "unused_days": unused or old.get("unused_days")}
    stored = {"table": state["table"], "header_map": state["header_map"], "limits": limits, "scan": state.get("scan")}
    return {"limits": limits, **_rendered(stored, deps, request)}


def _sentinel(state: TrustState, deps: TurnDeps, audit: models.Audit, answer: str) -> dict:
    head = answer.split(":", 1)[0].strip().upper()
    rest = answer.split(":", 1)[1].strip() if ":" in answer else ""
    today = deps.now.date()
    if head == "WHY":
        named = _named(audit, rest)
        return {"reply": render.why(named, today) if named else f"I can't find a dashboard called \"{render.safe(rest)}\""
                         " in this inventory. " + prompts.UNCLEAR}
    if head == "RETIRE_LIST":
        return {"reply": render.retire_list(audit, today), "envelope": models.envelope(audit)}
    if head == "THRESHOLD" and rest.isdigit() and 0 < int(rest) <= parse.MAX_DAYS:
        return _threshold(state, deps, int(rest), None, _request(state))
    if head == "DELETE":
        return {"reply": render.delete_declined(audit, today)}
    if head == "NEW_RUN":
        return {"route": "build"}
    if head == prompts.OFF_TOPIC:
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"reply": prompts.UNCLEAR}


def _named(audit: models.Audit, text: str) -> models.Dashboard | None:
    """The dashboard whose exact name the text contains (the longest such name)."""
    for d in sorted(audit.dashboards, key=lambda d: len(d.name), reverse=True):
        if re.search(rf"(?<![\w]){re.escape(d.name)}(?![\w])", text or "", re.IGNORECASE):
            return d
    return None
