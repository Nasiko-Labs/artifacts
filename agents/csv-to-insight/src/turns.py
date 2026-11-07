"""The graph's node bodies: analyse a table, follow up on a stored result, or scope-check a message that
came with no table. Each returns the state update; `scope` carries this turn's input and outputs that are
never persisted (the envelope data part and the PNG bytes)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import followup
import hitl
import llm
import pipeline
import prompts
import render
import smalltalk
from chart_png import render_png
from inputs import Incoming
from intake import Plan
from load import Loaded, parse_table
from plan import check_scope
from profiling import build
from result import Result

MIN_ROWS = 5
STORE_MAX_CHARS = 400_000     # the state API takes ~1 MB per body; larger tables are not kept for follow-ups
PAUSE_MAX_CHARS = 200_000


@dataclass
class Scope:
    deps: llm.TurnDeps
    incoming: Incoming
    plan: Plan | None = None
    data: dict | None = None
    png: bytes | None = None
    notes: list[str] = field(default_factory=list)


def _reply(scope: Scope, envelope: dict, results, view: str = "default", source: str = "") -> str:
    scope.data = envelope
    if scope.incoming.wants_json:
        return render.as_json(envelope)
    return render.render(envelope, results, view, source, extra_notes=scope.notes)


async def analyze(state: dict, scope: Scope, loaded: Loaded | None = None) -> dict:
    plan = scope.plan
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    loaded = loaded or await asyncio.to_thread(parse_table, plan.source, plan.delimiter)
    if len(loaded.rows) < MIN_ROWS or len(loaded.header) < 2:
        return {"reply": f"{prompts.TOO_FEW_ROWS} {smalltalk.INTRO}"}
    table = await asyncio.to_thread(build, loaded)
    analysis = await pipeline.analyze(scope.deps, table, plan.question, scope.incoming.context, plan.source,
                                      scope.incoming.wants_png, allow_pause=plan.allow_pause)
    if analysis.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if analysis.status == "missing_column":
        if len(plan.source) <= PAUSE_MAX_CHARS:
            pending = hitl.missing_column(plan.question, plan.source, plan.delimiter, analysis.term,
                                          table.profile.of("numeric"))
            return {"pause": pending.to_metadata()}
        analysis = await pipeline.analyze(scope.deps, table, plan.question, scope.incoming.context, plan.source,
                                          scope.incoming.wants_png, allow_pause=False)
        if analysis.status != "ok":
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
    scope.png = analysis.png
    kept = plan.source if len(plan.source) <= STORE_MAX_CHARS else ""
    reply = _reply(scope, analysis.envelope, analysis.results, source=plan.source)
    return {"reply": reply, "source": kept, "delimiter": plan.delimiter, "question": plan.question,
            "envelope": analysis.envelope, "results": [r.to_dict() for r in analysis.results]}


async def follow(state: dict, scope: Scope) -> dict:
    plan = scope.plan
    envelope = state.get("envelope")
    if not envelope:
        return {"reply": prompts.NO_TABLE_STORED}
    results = [Result.from_dict(r) for r in state.get("results") or []]
    if plan.route == "rerender":
        return _rerender(state, scope, envelope, results, plan.view)
    if not state.get("source"):
        return {"reply": prompts.TABLE_TOO_BIG_TO_KEEP}
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    loaded = await asyncio.to_thread(parse_table, state["source"], state.get("delimiter") or ",")
    if plan.route == "extra":
        table = await asyncio.to_thread(build, loaded)
        analysis = await pipeline.extend(scope.deps, table, envelope, results, plan.question, scope.incoming.context)
        if analysis.status == "off_topic":
            return {"reply": smalltalk.OFF_TOPIC_REPLY}
        if analysis.status != "ok":
            return {"reply": prompts.OFF_MENU.split(", so")[0] + "."}
        reply = _reply(scope, analysis.envelope, analysis.results, source=state["source"])
        return {"reply": reply, "envelope": analysis.envelope, "results": [r.to_dict() for r in analysis.results]}
    target = None
    if followup.wants_drill(plan.question):
        categories = (await asyncio.to_thread(build, loaded)).profile.of("category", "boolean")
        target = followup.drill_target(plan.question, loaded, categories)
    if target is not None:
        loaded = followup.filtered(loaded, *target)
    scope.plan = _with_source(plan, state)
    update = await analyze(state, scope, loaded)
    if "source" in update:  # the stored table stays the full one, even after a drill
        update["source"] = state["source"]
    return update


def _with_source(plan: Plan, state: dict) -> Plan:
    return Plan("analyze", source=state["source"], delimiter=state.get("delimiter") or ",", question=plan.question,
                allow_pause=False)


def _rerender(state: dict, scope: Scope, envelope: dict, results: list[Result], view: str) -> dict:
    if view == "png":
        scope.png = render_png(envelope.get("chart_data"))
        envelope = {**envelope, "chart_file": "chart.png" if scope.png else None}
        if scope.png is None:
            scope.notes.append(pipeline.PNG_UNAVAILABLE)
        return {"reply": _reply(scope, envelope, results, "chart"), "envelope": envelope}
    return {"reply": _reply(scope, envelope, results, view, state.get("source") or "")}


async def scope_check(state: dict, scope: Scope) -> dict:
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT}
    try:
        status = await check_scope(scope.deps, scope.plan.question, scope.incoming.context)
    except llm.LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"pause": hitl.Pending(hitl.NEED_SOURCE, question=scope.plan.question).to_metadata()}
