"""The board-metrics-updater LangGraph.

    START -+- (metrics in this context) -> followup -+- answered ---------------------------> END
           |                                         +- WRITE_SHEET -> sheet -> END (walk / pick)
           |                                         +- WRITE_DOC -> doc -> END (walk / pick)
           |                                         +- NEEDS_FETCH -+
           +- (no metrics yet) ---------------------------------------> fetch -+- figures -> END
                                                                               +- asked to write -> sheet | doc

fetch = discover -> subscriptions (paged) -> plan changes (last 30 days) -> compute (code) -> narrative
(1 call + code check) -> render. A greeting never enters the graph (smalltalk.py).

State is checkpointed per A2A contextId, so a follow-up and a write use the numbers already shown. The
graph is compiled per turn because its nodes close over that turn's dependencies; the checkpointer is
shared. Writes leave the graph in the turn result; the approve -> write walk runs outside it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import mrr
import narrative
import period as periods
import prompts
import render
import smalltalk
import steps
import subs
import writeflow
from deps import TurnDeps
from discovery import discover
from writes import Write

__all__ = ["MetricsAgent", "TurnDeps", "TurnResult"]

log = logging.getLogger("board_metrics.graph")

TURN_DEADLINE_SECS = 360
OUT_OF_TIME = "I ran out of time before finishing this run. Try a shorter period."


class MetricsState(TypedDict, total=False):
    query: str
    reply: str
    route: str
    request: dict
    report: dict             # render.Report of the metrics last shown
    sheet_name: str
    doc_name: str
    outcomes: dict           # write key -> written / skipped / denied / blocked / not_written
    writes: list[dict]       # this turn: writes ready for the approval walk
    question: dict | None    # this turn: a pick question
    envelope: dict | None


@dataclass(frozen=True)
class TurnResult:
    reply: str
    writes: tuple[Write, ...] = ()
    question: dict | None = None
    envelope: dict | None = None


class _Scope:
    def __init__(self, deps: TurnDeps):
        self.deps = deps


class MetricsAgent:
    def __init__(self, checkpointer: Any):
        self._checkpointer = checkpointer

    async def turn(self, query: str, context_id: str, deps: TurnDeps,
                   metadata: Mapping[str, Any] | None = None) -> TurnResult:
        canned = smalltalk.reply_to(query)
        if canned is not None:  # a greeting or small talk: no tool call, no LLM call, state untouched
            return TurnResult(canned)
        graph = _build_graph(_Scope(deps)).compile(checkpointer=self._checkpointer)
        request = subs.parse_request(query, metadata)
        fresh = {"query": query, "reply": "", "route": "", "request": request.__dict__, "writes": [],
                 "question": None, "envelope": None}
        try:
            final = await asyncio.wait_for(graph.ainvoke(fresh, _config(context_id), durability="exit"),
                                           TURN_DEADLINE_SECS)
        except TimeoutError:
            log.warning("turn passed its %ss deadline", TURN_DEADLINE_SECS)
            return TurnResult(OUT_OF_TIME)
        return TurnResult(final.get("reply") or "", tuple(Write.from_dict(w) for w in final.get("writes") or ()),
                          final.get("question"), final.get("envelope"))

    async def write_to(self, kind: str, name: str, context_id: str, deps: TurnDeps, *,
                       target: Mapping[str, Any] | None = None, default_order: bool = False) -> TurnResult:
        """The answer to a pick (a sheet or doc name) or to the default-order question."""
        values = await self._values(context_id)
        if not values.get("report"):
            return TurnResult(prompts.NO_METRICS_YET)
        flow = await writeflow.run(deps, render.Report.from_dict(values["report"]), kind, name,
                                   dict(values.get("outcomes") or {}), target=target, default_order=default_order)
        if flow.remember:
            await self._remember(context_id, flow.remember)
        return TurnResult(flow.reply, flow.writes, flow.question)

    async def record_outcomes(self, context_id: str, outcomes: Mapping[str, str]) -> None:
        if not outcomes:
            return
        values = await self._values(context_id)
        await self._remember(context_id, {"outcomes": {**dict(values.get("outcomes") or {}), **dict(outcomes)},
                                          "writes": []})

    async def _remember(self, context_id: str, update: Mapping[str, Any]) -> None:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        await graph.aupdate_state(_config(context_id), dict(update), as_node="fetch")

    async def _values(self, context_id: str) -> dict:
        graph = _build_graph(None).compile(checkpointer=self._checkpointer)
        return dict((await graph.aget_state(_config(context_id))).values)


def _config(context_id: str) -> dict:
    return {"configurable": {"thread_id": context_id}}


def _request(state: MetricsState) -> subs.Request:
    return subs.Request(**dict(state.get("request") or {}))


def _build_graph(scope: _Scope | None) -> StateGraph:
    async def followup(state: MetricsState) -> dict:
        return await _followup(state, scope.deps)

    async def fetch(state: MetricsState) -> dict:
        return await _fetch(state, scope.deps)

    async def sheet(state: MetricsState) -> dict:
        return await _write(state, scope.deps, "sheet")

    async def doc(state: MetricsState) -> dict:
        return await _write(state, scope.deps, "doc")

    graph = StateGraph(MetricsState)
    for name, node in (("followup", followup), ("fetch", fetch), ("sheet", sheet), ("doc", doc)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _entry, ["followup", "fetch"])
    graph.add_conditional_edges("followup", _route, ["fetch", "sheet", "doc", END])
    graph.add_conditional_edges("fetch", _route, ["sheet", "doc", END])
    graph.add_edge("sheet", END)
    graph.add_edge("doc", END)
    return graph


def _entry(state: MetricsState) -> str:
    return "followup" if state.get("report") and not subs.pasted_subs(state["query"]) else "fetch"


def _route(state: MetricsState) -> str:
    return state.get("route") or END


# --- follow-ups -------------------------------------------------------------------------------------------

async def _followup(state: MetricsState, deps: TurnDeps) -> dict:
    query, request = state["query"], _request(state)
    if request.number_edit:  # parsed in code: no LLM call
        return {"reply": prompts.NUMBERS_FIXED.split(" Add the row")[0], "route": ""}
    if request.wants_doc:
        return {"route": "doc"}
    if request.wants_sheet:
        return {"route": "sheet"}
    if request.names_period:
        return {"route": "fetch"}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    report = render.Report.from_dict(state["report"])
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(render.render_report(report, False))}"
    text = await steps.ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    for sentinel, outcome in ((prompts.OFF_TOPIC, {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}),
                              (prompts.NEEDS_FETCH, {"route": "fetch"}), (prompts.WRITE_SHEET, {"route": "sheet"}),
                              (prompts.WRITE_DOC, {"route": "doc"})):
        if text.startswith(sentinel):
            return outcome
    return {"reply": text or prompts.UNREADABLE_ANSWER, "route": ""}


async def _write(state: MetricsState, deps: TurnDeps, kind: str) -> dict:
    report = render.Report.from_dict(state.get("report") or {})
    if not report.blocks:
        return {"reply": state.get("reply") or prompts.NO_METRICS_YET, "route": ""}
    remembered = state.get("sheet_name" if kind == "sheet" else "doc_name")
    name = _request(state).target_name or remembered or None
    flow = await writeflow.run(deps, report, kind, name, dict(state.get("outcomes") or {}))
    lead = state.get("reply") or ""
    reply = "\n\n".join(p for p in (lead, flow.reply) if p)
    return {**dict(flow.remember), "reply": reply, "writes": [w.to_dict() for w in flow.writes],
            "question": flow.question, "route": ""}


# --- the computation ---------------------------------------------------------------------------------------

async def _fetch(state: MetricsState, deps: TurnDeps) -> dict:
    request = _request(state)
    found = periods.parse(request.period_text, deps.now)
    t0, t1 = found.t0(), found.t1(deps.now)
    pasted = subs.pasted_subs(state["query"])
    if pasted:
        blocks = mrr.compute(pasted, (), t0, t1, changes_known=False)
        report = render.Report(period=found.to_dict(), blocks=_only(blocks, request), pasted=True)
        return await _finish(state, deps, report, use_model=bool(deps.traceparent))
    catalog, failure = await discover(deps, "read")
    if failure is not None:
        return {"reply": _refusal(failure, state["query"]), "route": ""}
    reader = steps.reader_for(deps, catalog)
    outcome = await steps.fetch_subscriptions(deps, reader, state["query"], t0, t1)
    if outcome.reply is not None:
        reply = smalltalk.OFF_TOPIC_REPLY if outcome.reply == prompts.OFF_TOPIC else outcome.reply
        return {"reply": _refusal(reply, state["query"]), "route": ""}
    changes, changes_known, notes = await _changes(deps, reader, outcome.subs, t1)
    window_start = deps.now - timedelta(days=prompts.CHANGES_WINDOW_DAYS)
    blocks = mrr.compute(outcome.subs, changes, t0, t1, changes_known=changes_known)
    report = render.Report(period=found.to_dict(), blocks=_only(blocks, request), livemode=outcome.livemode,
                           approximate=t0 < window_start or not changes_known,
                           pages_left_out=outcome.pages_left_out, dropped=outcome.dropped, notes=notes)
    return await _finish(state, deps, report, use_model=True)


async def _changes(deps: TurnDeps, reader: steps.Reader, found: tuple[subs.Sub, ...],
                   t1) -> tuple[tuple[subs.Change, ...], bool, tuple[str, ...]]:
    if t1 <= deps.now - timedelta(days=prompts.CHANGES_WINDOW_DAYS):
        return (), True, ()  # nothing in the event window: current items, flagged approximate
    if deps.elapsed() > steps.CHANGES_TIME_BUDGET_SECS:
        log.info("subscriptions ended after %.0fs; skipping plan changes", deps.elapsed())
        return (), False, (prompts.CHANGES_SKIPPED_NOTE,)
    changes = await steps.fetch_changes(deps, reader, found)
    return (changes or ()), changes is not None, ()


def _only(blocks: tuple[mrr.Block, ...], request: subs.Request) -> tuple[mrr.Block, ...]:
    if request.currency is None:
        return blocks
    return tuple(b for b in blocks if b.currency == request.currency)


async def _finish(state: MetricsState, deps: TurnDeps, report: render.Report, use_model: bool) -> dict:
    request = _request(state)
    if use_model:
        paragraph, checked = await steps.write_narrative(deps, report)
    else:
        paragraph, checked = (narrative.template(report) if report.blocks else ""), False
    report = render.Report(**{**report.__dict__, "narrative": paragraph, "narrative_checked": checked})
    env = render.envelope(report)
    if not report.blocks:
        reply = f"No subscriptions in {request.currency or 'any currency'} for {report.label}."
    elif request.json_mode:
        reply = render.json_block(env)
    else:
        reply = render.render_report(report, has_sheet_hint=not (request.wants_sheet or request.wants_doc))
    route = "doc" if request.wants_doc else "sheet" if request.wants_sheet else ""
    return {"report": report.to_dict(), "outcomes": {}, "reply": reply, "envelope": env, "route": route}


def _refusal(reply: str, query: str) -> str:
    """A refusal that ends the turn before any model call still declines an off-topic message as such."""
    unreadable = reply.startswith((prompts.NO_PAYMENTS_TOOL, prompts.GATEWAY_UNREACHABLE))
    return smalltalk.OFF_TOPIC_REPLY if unreadable and not subs.mentions_domain(query) else reply
