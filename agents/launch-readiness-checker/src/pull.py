"""A fresh pull for one launch: discover the read tools, run the four area steps concurrently (an area pasted
by the user skips its step), then rules, verdict and rendering in code."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import checks
import coverage
import parse_paste
import render
import replies
import steps_docs
import steps_eng
import steps_marketing
import steps_support
import verdict as v
from deps import TurnDeps
from discovery import SOURCES, discover
from reader import StepOutcome
from records import Article, Macro
from scope import Request

log = logging.getLogger("launch_readiness.pull")
_STEPS = {"engineering": steps_eng.run, "docs": steps_docs.run, "support": steps_support.run,
          "marketing": steps_marketing.run}
_SCHEDULE = re.compile(r"\b(?:daily|every (?:day|morning|week)|weekly|on a schedule|automatically|monitor)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Pulled:
    reply: str
    envelope: dict | None = None
    text: str = ""


async def pull(deps: TurnDeps, request: Request, query: str) -> Pulled:
    pasted = parse_paste.rows_for(request.pasted)
    catalog, failure = await discover(deps)
    if failure is not None:
        return Pulled(failure)
    to_run = [s for s in SOURCES if s not in pasted and catalog.for_source(s)]
    outcomes = await asyncio.gather(*(_STEPS[s](deps, catalog, request) for s in to_run))
    by_area = {o.source: o for o in outcomes}
    statuses = tuple(_status(s, pasted, by_area) for s in SOURCES)
    inputs = _inputs(deps, request, pasted, by_area, statuses)
    rows = checks.evaluate(inputs)
    meta = _meta(request, query, statuses, outcomes, pasted)
    text = render.render(request.launch, request.launch_date, deps.today, rows, meta, statuses)
    data = render.envelope(request.launch, request.launch_date, deps.today, rows, meta)
    return Pulled(render.as_json(data) if request.json_mode else text, data, text)


def _status(source: str, pasted: dict, by_area: dict[str, StepOutcome]) -> coverage.SourceStatus:
    if source in pasted:
        return coverage.SourceStatus(source, "pasted")
    outcome = by_area.get(source)
    return outcome.status if outcome else coverage.SourceStatus(source, "missing")


def _reason(status: coverage.SourceStatus) -> str:
    return "no tool connected" if status.state == "missing" else status.reason or "unavailable"


def _inputs(deps: TurnDeps, request: Request, pasted: dict, by_area: dict[str, StepOutcome],
            statuses: tuple[coverage.SourceStatus, ...]) -> checks.Inputs:
    ok = {s.source for s in statuses if s.ok}

    def rows_of(area: str):
        if area in pasted:
            return pasted[area]
        return by_area[area].parsed.rows if area in ok else None

    support = rows_of("support")
    return checks.Inputs(
        launch=request.launch, today=deps.today, launch_date=request.launch_date,
        issues=rows_of("engineering"), docs=rows_of("docs"),
        macros=tuple(r for r in support if isinstance(r, Macro)) if support is not None else None,
        articles=tuple(r for r in support or () if isinstance(r, Article)),
        tasks=rows_of("marketing"), marketing_project=request.hints.get("marketing project"),
        reasons={s.source: _reason(s) for s in statuses if not s.ok})


def _meta(request: Request, query: str, statuses, outcomes, pasted: dict) -> v.RunMeta:
    read = tuple(s.source for s in statuses if s.ok)
    unread = tuple(s.source for s in statuses if not s.ok)
    salvaged = tuple(o.source for o in outcomes if o.salvaged)
    capped = any(o.parsed.capped for o in outcomes) or any(
        getattr(d, "capped", False) for o in outcomes for d in o.parsed.rows)
    notes = tuple(replies.STEP_LIMIT_NOTE.format(area=a) for a in salvaged)
    notes += tuple(f"{a.capitalize()} read from your pasted checklist." for a in pasted)
    notes += (replies.PASTE_TIP,) if unread else ()
    notes += (replies.NO_SCHEDULE,) if _SCHEDULE.search(query or "") else ()
    notes += ("Your message was longer than 100,000 characters; only the first 100,000 were read.",) \
        if request.truncated else ()
    return v.RunMeta(read, unread, capped, sum(o.parsed.extracted for o in outcomes),
                     sum(o.parsed.dropped for o in outcomes), bool(salvaged), _ambiguous(request), notes)


def _ambiguous(request: Request) -> bool:
    """A launch name with no token long enough to match on (like "AI") can match unrelated rows."""
    return all(len(t) < 3 for t in (request.launch or "").split())
