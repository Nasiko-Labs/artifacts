"""One analysis turn over a typed table:

    plan (LLM) → validate (code) → run analyses (code) → findings (LLM) → check → chart (code) → envelope

At most 4 model calls (plan and findings, one repair each). `extend` adds one analysis to a stored result
("also show X by Y") with one plan call; its findings are the facts' own sentences."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import caveats as cv
import envelope as env
import llm
import prompts
from chart_png import render_png
from chart_text import render_chart
from checks import fact_findings
from findings import lead_facts, write_findings
from menu import chart_kind
from plan import Planned, make_plan
from profiling import Table
from result import Result
from run import run_plan

PNG_UNAVAILABLE = "PNG unavailable: I couldn't draw the chart file, so the text chart is all there is."
_WHY = re.compile(r"\bwhy\b|\bcause[sd]?\b|\bwhat drives\b|\bdriv(?:er|ers|ing)\b|\breason", re.IGNORECASE)


@dataclass(frozen=True)
class Analysis:
    status: str                              # ok | off_topic | missing_column | nothing
    term: str = ""
    envelope: dict | None = None
    results: tuple[Result, ...] = ()
    png: bytes | None = None


async def analyze(deps: llm.TurnDeps, table: Table, question: str, context: Sequence[str] = (),
                  source: str = "", wants_png: bool = False, allow_pause: bool = True) -> Analysis:
    planned = await make_plan(deps, table.profile, question, context, allow_pause=allow_pause)
    if planned.status != "ok":
        return Analysis(planned.status, planned.term)
    ran = await run_plan(table, planned.items, clock=deps.clock)
    checked = await write_findings(deps, ran.results, question, context, source)
    chart = pick_chart(planned, ran.results)
    notes = list(table.notes)
    if planned.used_default and question.strip():
        notes.append(prompts.OFF_MENU)
    data_caveats = list(cv.data_caveats(table, ran.results))
    if _WHY.search(question or ""):
        data_caveats.append(prompts.CAUSES_NOTE)
    png = render_png(chart.chart) if wants_png and chart else None
    if wants_png and png is None:
        notes.append(PNG_UNAVAILABLE)
    signals = env.Signals(planned.used_default, planned.dropped, wants_png and png is None)
    envelope = env.build(table, ran.results, ran.skipped, checked, data_caveats, chart, render_chart(chart),
                         signals, png=png is not None, notes=notes)
    return Analysis("ok", envelope=envelope, results=ran.results, png=png)


def pick_chart(planned: Planned, results: Sequence[Result]) -> Result | None:
    if planned.chart is not None:
        item = planned.items[planned.chart]
        match = next((r for r in results if r.kind == item.kind and r.columns == item.columns), None)
        if match is not None and match.chart:
            return match
    charted = [r for r in results if r.chart and chart_kind(r.kind) != "none"]
    return next((r for r in charted if r.kind == "trend"), charted[0] if charted else None)


async def extend(deps: llm.TurnDeps, table: Table, stored: dict, stored_results: Sequence[Result], question: str,
                 context: Sequence[str] = ()) -> Analysis:
    planned = await make_plan(deps, table.profile, question, context, limit=1, allow_pause=False, fallback=False)
    if planned.status != "ok":
        return Analysis(planned.status)
    done = {(a["kind"], tuple(sorted(a["columns"].items()))) for a in stored.get("analyses_run", [])}
    items = [i for i in planned.items if (i.kind, tuple(sorted(i.columns.items()))) not in done]
    if not items:
        return Analysis("nothing")
    first_fact = 1 + sum(len(r.facts) for r in stored_results)
    ran = await run_plan(table, items, clock=deps.clock, first_result=len(stored_results) + 1,
                         first_fact=first_fact)
    if not ran.results:
        return Analysis("nothing")
    added = [f.to_dict() for f in fact_findings(lead_facts(ran.results))]
    envelope = dict(stored)
    envelope["findings"] = envelope["items"] = [*stored.get("findings", []), *added]
    envelope["analyses_run"] = [*stored.get("analyses_run", []),
                                *({"id": r.id, "kind": r.kind, "columns": dict(r.columns)} for r in ran.results)]
    new_chart = next((r for r in ran.results if r.chart and chart_kind(r.kind) != "none"), None)
    if new_chart is not None:
        envelope |= {"chart": render_chart(new_chart), "chart_data": dict(new_chart.chart),
                     "chart_result": new_chart.id, "chart_file": None}
    envelope["summary"] = f"{len(envelope['findings'])} findings; added {ran.results[0].title}."
    return Analysis("ok", envelope=envelope, results=(*stored_results, *ran.results))

