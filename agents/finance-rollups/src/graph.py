"""The finance-rollups LangGraph.

    START -+- (a rollup in this context, ledger rows pasted) ---------> reconcile (code) -> narrative -> END
           +- (a rollup in this context, nothing new) -> followup -+- answer / OFF_TOPIC / RECONCILE -> END
           |                                                       +- NEEDS_FETCH -+
           +- (otherwise) -----------------------------------------------------> run -> END

run = parse (code) -> [pasted summary rows: rollup from them] | discover -> balance (paged by week) -> invoices
(skipped after 270 s) -> rollup (code) -> reconcile (code, if a ledger was pasted) -> narrative (1 call + code
check) -> render. A greeting never enters the graph (smalltalk.py); a write request is refused in code.

State is checkpointed per A2A contextId, so a follow-up and a later ledger paste use the numbers already shown. The
graph is compiled per turn because its nodes close over that turn's dependencies; the checkpointer is shared.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import months as mo
import narrative
import prompts
import reconcile as rec
import render
import request as rq
import rollup
import smalltalk
import steps
from deps import INVOICES_CUTOFF_SECS, TURN_DEADLINE_SECS, TurnDeps
from discovery import discover
from render import Report

__all__ = ["FinanceAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("finance_rollups.graph")


class RollupState(TypedDict, total=False):
    query: str
    metadata: dict
    reply: str
    route: str
    envelope: dict | None
    report: dict             # kept: render.Report of the rollup last shown


@dataclass(frozen=True)
class TurnResult:
    reply: str
    envelope: dict | None = None


class FinanceAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps,
                   metadata: Mapping[str, Any] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        if rq.parse(query, deps.today, metadata).write_ask:
            return TurnResult(prompts.READ_ONLY_REPLY)
        graph = _build_graph(deps).compile(checkpointer=self._checkpointer)
        fresh = {"query": query, "metadata": dict(metadata or {}), "reply": "", "route": "", "envelope": None}
        try:
            final = await asyncio.wait_for(graph.ainvoke(fresh, {"configurable": {"thread_id": context_id}},
                                                         durability="exit"), TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(prompts.OUT_OF_TIME)
        return TurnResult(final.get("reply") or "", final.get("envelope"))


def _build_graph(deps: TurnDeps) -> StateGraph:
    async def followup(state: RollupState) -> dict:
        return await _followup(state, deps)

    async def reconcile(state: RollupState) -> dict:
        return await _reconcile(state, deps)

    async def run(state: RollupState) -> dict:
        return await _run(state, deps)

    graph = StateGraph(RollupState)
    for name, node in (("followup", followup), ("reconcile", reconcile), ("run", run)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, lambda s: _entry(s, deps), ["followup", "reconcile", "run"])
    graph.add_conditional_edges("followup", lambda s: s.get("route") or END, ["run", END])
    graph.add_edge("reconcile", END)
    graph.add_edge("run", END)
    return graph


def _request(state: RollupState, deps: TurnDeps) -> rq.Request:
    return rq.parse(state["query"], deps.today, state.get("metadata"))


def _entry(state: RollupState, deps: TurnDeps) -> str:
    request = _request(state, deps)
    if not state.get("report") or request.summary or request.names_range:
        return "run"
    return "reconcile" if request.ledger else "followup"


# --- follow-ups -----------------------------------------------------------------------------------------------

async def _followup(state: RollupState, deps: TurnDeps) -> dict:
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": "", "envelope": None}
    report = Report.from_dict(state["report"])
    text = await steps.answer_followup(deps, render.render_report(report, with_hint=False), state["query"])
    for sentinel, outcome in ((prompts.OFF_TOPIC, {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}),
                              (prompts.RECONCILE, {"reply": prompts.PASTE_LEDGER, "route": ""}),
                              (prompts.NEEDS_FETCH, {"route": "run"})):
        if text.startswith(sentinel):
            return {**outcome, "envelope": None}
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": "", "envelope": None}


async def _reconcile(state: RollupState, deps: TurnDeps) -> dict:
    request = _request(state, deps)
    report = Report.from_dict(state["report"])
    tied = report.with_(reconciliation=rec.reconcile(request.ledger, report.rollups, report.invoices, report.months),
                        ledger_given=True, narrative="")
    return await _finish(tied, request, deps, use_model=bool(deps.traceparent))


# --- the run --------------------------------------------------------------------------------------------------

async def _run(state: RollupState, deps: TurnDeps) -> dict:
    request = _request(state, deps)
    if request.summary:
        return await _from_summary(request, deps)
    query = state["query"]
    if not rq.mentions_domain(query):
        if not deps.traceparent:
            return _reply(prompts.NO_TRACE_CONTEXT)
        if not await steps.in_scope(deps, query):
            return _reply(smalltalk.OFF_TOPIC_REPLY)
    catalog, failure = await discover(deps)
    if failure is not None:
        return _reply(failure)
    reader = steps.reader_for(deps, catalog)
    start, end = mo.bounds(request.range.months[0])[0], mo.bounds(request.range.months[-1])[1]
    outcome = await steps.fetch_balance(deps, reader, query, start, end)
    if outcome.off_topic:
        return _reply(smalltalk.OFF_TOPIC_REPLY)
    if outcome.reply is not None:
        return _reply(outcome.reply)
    capped = rollup.cap(outcome.checked.rows, rollup.ROW_CAP, lambda row: row.created_at, outcome.has_more)
    if not capped.rows:
        return _reply(prompts.NO_ACTIVITY.format(label=mo.range_label(request.range.months)))
    invoices, in_range, notes = await _invoices(deps, reader, start, end)
    partial_from = min((p for p in (capped.partial_from, in_range.partial_from) if p), default=None)
    rollups = rollup.compute(capped.rows, None if invoices is None else in_range.rows, request.range.months,
                             partial_from)
    report = Report(months=request.range.months, rollups=rollups, invoices=invoices or (), livemode=outcome.livemode,
                    invoices_known=invoices is not None, remaining=-1 if outcome.has_more else capped.remaining,
                    dropped=outcome.checked.dropped, inconsistent=outcome.checked.inconsistent,
                    mapped=outcome.checked.mapped, clamped=request.range.clamped, notes=notes)
    report = render.only_currency(report, request.currency)
    if request.ledger:
        report = report.with_(reconciliation=rec.reconcile(request.ledger, report.rollups, report.invoices,
                                                           report.months), ledger_given=True)
    return await _finish(report, request, deps, use_model=True)


async def _invoices(deps: TurnDeps, reader: steps.Reader, start: int, end: int):
    """(every checked invoice incl. the boundary margins or None, the capped in-range ones, notes)."""
    if deps.elapsed() > INVOICES_CUTOFF_SECS:
        log.info("balance step ended after %.0fs; skipping invoices", deps.elapsed())
        return None, rollup.Capped(()), (prompts.INVOICES_SKIPPED_NOTE,)
    found = await steps.fetch_invoices(deps, reader, start, end)
    if found.invoices is None:
        return None, rollup.Capped(()), (prompts.INVOICES_NOT_READ_NOTE,)
    inside = tuple(i for i in found.invoices if start <= i.paid_at < end)
    return found.invoices, rollup.cap(inside, rollup.INVOICE_CAP, lambda i: i.paid_at, found.has_more), ()


async def _from_summary(request: rq.Request, deps: TurnDeps) -> dict:
    rollups = rollup.from_summary(request.summary)
    month_keys = render.months_of(rollups)
    report = Report(months=month_keys, rollups=rollups, source=render.SOURCE_PASTED, invoices_known=True)
    report = render.only_currency(report, request.currency)
    if request.ledger:
        report = report.with_(reconciliation=rec.reconcile(request.ledger, report.rollups, (), month_keys),
                              ledger_given=True)
    return await _finish(report, request, deps, use_model=bool(deps.traceparent))


async def _finish(report: Report, request: rq.Request, deps: TurnDeps, use_model: bool) -> dict:
    if not report.rollups:
        return _reply(f"No activity in {request.currency or 'any currency'} for {report.label}.")
    if use_model:
        text, checked = await steps.write_narrative(deps, report)
    else:
        text, checked = narrative.template(report), False
    report = report.with_(narrative=text, narrative_checked=checked)
    env = render.envelope(report)
    reply = render.json_block(env) if request.json_mode else render.render_report(report)
    return {"report": report.to_dict(), "reply": reply, "envelope": env, "route": ""}


def _reply(text: str) -> dict:
    return {"reply": text, "route": "", "envelope": None}
