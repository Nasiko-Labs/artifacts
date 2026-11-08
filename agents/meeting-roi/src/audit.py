"""Turn 1: discover → calendar → group → notes → chat → decisions → compute → render.

Each step reads only its own source through the read-only gateway; every record is checked in code
before any number is computed; the proposal rules and the report are code. A missing calendar ends the
turn with a fixed reply (or scores a pasted list); a missing notes or chat source is a coverage line."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

import checks
import coverage as coverage_
import prompts
import series as series_
import smalltalk
import steps
from deps import TurnDeps
from discovery import RECOVER_META, Catalog, discover
from pasted import facts_from_rows
from records import Decisions, NotesFinding, Report
from render import excluded_text
from request import Request
from roi import Evidence, evaluate, facts_from_series
from steps import StepResult

log = logging.getLogger("meeting_roi.audit")

MAX_DECISION_CALLS = 25
MEETING_WORDS = re.compile(
    r"\b(?:meetings?|calendar|syncs?|stand-?ups?|1:1s?|one-on-ones?|recurring|audit|notes|attendees|invitees|"
    r"cancel|shorten|shrink|hours|series|rsvps?|agenda)\b", re.IGNORECASE)


@dataclass(frozen=True)
class AuditOutcome:
    reply: str
    report: Report | None = None


async def run_audit(deps: TurnDeps, req: Request) -> AuditOutcome:
    catalog, failure = await discover(deps)
    if failure is not None:
        return AuditOutcome(failure)
    if req.pasted:  # the user gave the data: score it, RSVP and size only
        return AuditOutcome("", pasted_report(req, deps))
    end = deps.now
    start = end - timedelta(days=req.window_days)
    calendar = await _step(deps, catalog, "calendar", prompts.calendar_system(start, end), req.text)
    if calendar.status == "off_topic":
        return AuditOutcome(smalltalk.OFF_TOPIC_REPLY)
    if calendar.status == "paused":
        return AuditOutcome(prompts.READ_NEEDS_APPROVAL)
    if calendar.status != "ok" or _cov([calendar]).statuses[0].state != "ok":
        return await _no_calendar(deps, req, _cov([calendar]))
    events, events_capped = checks.check_events(calendar.answer, calendar.seen, start, end)
    events = checks.without_self_declined(events)
    dates = {"start": start.date().isoformat(), "end": end.date().isoformat()}
    if not events:
        return AuditOutcome(prompts.NO_EVENTS.format(**dates))
    grouped = series_.select(series_.group(events), series_.user_domain(events), only_organizer=req.only_organizer,
                             min_people=req.min_people)
    if not grouped.series:
        return AuditOutcome(prompts.NO_SERIES.format(events=len(events), **dates) + _excluded_line(grouped.excluded))
    picked = grouped.series
    docs = await _step(deps, catalog, "docs", prompts.notes_system(
        [(s.title, s.attachments) for s in picked], start, end, req.notes_link), req.text)
    chat = await _step(deps, catalog, "chat", prompts.chat_system(
        [(s.title, s.channel_hint, [e.start.isoformat() for e in s.events]) for s in picked], start, end), req.text)
    cov = _cov([calendar, docs, chat])
    states = {s.source: s.state for s in cov.statuses}
    found = checks.check_docs(docs.answer, docs.seen, picked) if states["docs"] == "ok" else {}
    decided, timed_out = await _decisions(deps, found, docs.seen)
    followups = checks.check_chat(chat.answer, chat.seen, picked) if states["chat"] == "ok" else {}
    notes_state = "read" if states["docs"] == "ok" else ("skipped" if states["docs"] == "skipped" else "missing")
    meetings = tuple(
        evaluate(facts_from_series(s), Evidence(notes_state, found.get(s.title), decided.get(s.title, Decisions()),
                                                followups.get(s.title)), req.window_days, req.rate)
        for s in picked)
    lines = (*coverage_.notes(cov), *_partial_lines([calendar, docs, chat]),
             *((prompts.TIME_LIMIT_NOTE.format(what="Decisions for some meetings"),) if timed_out else ()))
    report = Report(meetings=meetings, window_days=req.window_days, window_start=start, window_end=end,
                    excluded=dict(grouped.excluded), coverage=lines, sources_read=cov.covered,
                    capped=bool(grouped.capped) or events_capped, rate=req.rate, filters=req.filters)
    return AuditOutcome("", report)


async def _no_calendar(deps: TurnDeps, req: Request, cov: coverage_.Coverage) -> AuditOutcome:
    """No calendar could be read: end with a fixed reply (or decline an off-topic message)."""
    failed = cov.statuses[0]
    if failed.state != "missing":
        return AuditOutcome(f"{prompts.CALENDAR_UNREADABLE}\n\n- {coverage_.line(failed)}")
    if not MEETING_WORDS.search(req.text):  # the calendar step never ran, so it could not flag off-topic
        verdict = await steps.ask_once(deps, ({"role": "system", "content": prompts.SCOPE_SYSTEM},
                                              {"role": "user", "content": req.text}))
        if not verdict.startswith(prompts.IN_SCOPE):
            return AuditOutcome(smalltalk.OFF_TOPIC_REPLY)
    return AuditOutcome(prompts.NO_CALENDAR)


def pasted_report(req: Request, deps: TurnDeps) -> Report:
    facts, capped = facts_from_rows(req.pasted)
    meetings = tuple(evaluate(f, Evidence("missing"), req.window_days, req.rate) for f in facts)
    end = deps.now
    note = ("Scored from the list you pasted, on RSVP and size only: no calendar, notes or chat were read, so no "
            "cancel is proposed.")
    return Report(meetings=meetings, window_days=req.window_days, window_start=end - timedelta(days=req.window_days),
                  window_end=end, coverage=(note,), capped=bool(capped), pasted=True, rate=req.rate,
                  filters=req.filters)


async def _decisions(deps: TurnDeps, found: Mapping[str, NotesFinding],
                     seen: checks.Seen) -> tuple[dict[str, Decisions], bool]:
    """One tool-less call per notes doc with text (max 25); quotes checked verbatim in code."""
    out: dict[str, Decisions] = {}
    timed_out = False
    for n, (title, notes) in enumerate(found.items()):
        if not notes.text:
            continue
        if n >= MAX_DECISION_CALLS or deps.past_deadline():
            timed_out = True
            continue
        answer = checks.parse_answer(await steps.ask_once(deps, (
            {"role": "system", "content": prompts.DECISIONS_SYSTEM},
            {"role": "user", "content": f"Meeting: {title}\n{prompts.fence_notes(notes.text)}"})))
        out[title] = checks.check_decisions(answer, seen, notes.text)
    return out, timed_out


async def _step(deps: TurnDeps, catalog: Catalog, source: str, system: str, query: str) -> StepResult:
    return await steps.run_step(deps, source, catalog.tools_for(source), catalog.writes, system, query)


def _cov(results: Sequence[StepResult]) -> coverage_.Coverage:
    offered = {r.source: r.offered for r in results if r.status != "no_tool"}
    dropped = {k: v for r in results for k, v in r.dropped.items()}
    overrides = {r.source: r.status for r in results if r.status in ("skipped", "unreadable")}
    cov = coverage_.assess(offered, dropped, overrides, frozenset({RECOVER_META}))
    present = {r.source for r in results}
    return coverage_.Coverage(tuple(s for s in cov.statuses if s.source in present))


def _partial_lines(results: Sequence[StepResult]) -> tuple[str, ...]:
    names = {"calendar": "your calendar", "docs": "notes documents", "chat": "chat"}
    return tuple(prompts.STEP_LIMIT_NOTE.format(source=names[r.source]) for r in results if r.partial)


def _excluded_line(excluded: Mapping[str, int]) -> str:
    text = excluded_text(excluded)
    return f"\n\n{text}" if text else ""
