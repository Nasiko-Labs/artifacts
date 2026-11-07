"""One planning turn: parse (code) → discover → fetch (tool loop) → place + gaps + flags (code) → context
(LLM, verified in code) → render (code). Also the pasted-meetings fallback and the timezone resume.

LLM budget per turn: MAX_LLM_CALLS (fetch rounds + context batches + one repair). Tool budget: fetch.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, time

import context as ctx
import dayplan
import discovery
import envelope
import events as ev
import nasiko_hitl
import parse
import paste
import prompts
import render
import smalltalk
import timewin
from deps import TurnDeps
from fetch import fetch
from llm import ask_once
from models import Plan, RawEvent

log = logging.getLogger("day_planner.planner")

MAX_LLM_CALLS = 4


@dataclass(frozen=True)
class Settings:
    """What this conversation remembers: the zone (Zone.to_dict) and the working hours."""

    zone: dict | None = None
    hours: tuple[str, str] | None = None

    def working_hours(self, stated: tuple[time, time] | None) -> tuple[time, time]:
        if stated is not None:
            return stated
        if self.hours:
            return time.fromisoformat(self.hours[0]), time.fromisoformat(self.hours[1])
        return parse.DEFAULT_HOURS


@dataclass(frozen=True)
class Outcome:
    reply: str
    plan: Plan | None = None
    envelope: dict | None = None
    question: dict | None = None     # question_metadata for an input-required pause
    pending: dict | None = None      # kept in the checkpointer until the question is answered
    zone: dict | None = None         # the zone to remember
    hours: tuple[str, str] | None = None


async def plan_turn(deps: TurnDeps, query: str, settings: Settings) -> Outcome:
    request = parse.parse_request(query)
    hours = settings.working_hours(request.hours)
    stored_hours = _fmt(hours) if request.hours else settings.hours
    if paste.looks_pasted(query):
        return replace(_pasted(deps, query, request, settings, hours), hours=stored_hours)
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT)
    catalog, failure = await discovery.discover(deps)
    if failure is not None or not catalog.tools:
        line = prompts.GATEWAY_DOWN if failure == "unreachable" else prompts.NO_CALENDAR
        return await _unavailable(deps, query, line)
    zone = ev.timezone_from(ev.Extracted((), None), request.stated_tz, settings.zone)
    days = parse.resolve_days(request, deps.now.astimezone(zone.tz if zone else UTC).date())
    window = timewin.local_days(days, zone.tz) if zone else timewin.band(days)
    fetched = await fetch(deps, catalog, window, zone.name if zone else None, query)
    if fetched.status == "off_topic":
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    if fetched.status == "paused":
        return Outcome(prompts.READ_NEEDS_APPROVAL + "\n\n" + prompts.PASTE_PROMPT)
    if fetched.status == "dead":
        return Outcome(f"I couldn't read your calendar, so I won't guess.\n\nCoverage: calendar: unavailable "
                       f"({fetched.reason}).\n\n{prompts.PASTE_PROMPT}")
    coverage = _coverage(fetched.dropped)
    zone = ev.timezone_from(fetched.extracted, request.stated_tz, settings.zone)
    pending = {"query": query, "events": [e.to_dict() for e in fetched.extracted.events],
               "coverage": list(coverage), "hours": list(_fmt(hours))}
    if zone is None:
        return Outcome(prompts.TZ_HEADER, question=_tz_question(prompts.TZ_HEADER), pending=pending,
                       hours=stored_hours)
    outcome = await finish(deps, pending, zone, used_calls=fetched.llm_calls, tz_asked=False)
    return replace(outcome, hours=stored_hours)


async def resume(deps: TurnDeps, answer: str, pending: dict) -> Outcome | None:
    """The timezone answer. None means the answer is not a zone (handle it as a new message)."""
    name = timewin.valid_zone_name(answer.strip()) or timewin.stated_timezone(answer)
    if name is None:
        if answer.strip().casefold() == "other":
            return Outcome(prompts.TZ_RETRY, question=_tz_question(prompts.TZ_RETRY), pending=pending)
        return None
    if not deps.traceparent:
        return Outcome(prompts.NO_TRACE_CONTEXT, pending=pending)
    zone = timewin.Zone(timewin.zone_of(name), name, "stated")
    return await finish(deps, pending, zone, used_calls=0, tz_asked=True)


async def finish(deps: TurnDeps, pending: dict, zone: timewin.Zone, *, used_calls: int, tz_asked: bool) -> Outcome:
    request = parse.parse_request(pending["query"])
    hours = tuple(time.fromisoformat(h) for h in pending["hours"])
    days = parse.resolve_days(request, deps.now.astimezone(zone.tz).date())
    raws = tuple(RawEvent.from_dict(e) for e in pending.get("events") or ())
    placed = ev.place(raws, days, zone.tz)
    plans = dayplan.build_days(placed, days, zone.tz, hours=hours, part=request.part, min_gap=request.min_gap)
    timed = [e for d in plans for e in d.events]
    found = await ctx.contexts(deps, timed, MAX_LLM_CALLS - used_calls)
    plans = tuple(dayplan.with_context(d, found.lines) for d in plans)
    coverage = tuple(pending.get("coverage") or ())
    plan = Plan(days=plans, zone=zone.to_dict(), coverage=coverage, context_dropped=found.dropped,
                tz_asked=tz_asked, tools_failed=bool(coverage))
    return Outcome(render.render_plan(plan, zone.tz), plan, envelope.build(plan, zone.tz), zone=zone.to_dict())


def _pasted(deps: TurnDeps, query: str, request: parse.Request, settings: Settings,
            hours: tuple[time, time]) -> Outcome:
    zone = ev.timezone_from(ev.Extracted((), None), request.stated_tz, settings.zone) or timewin.Zone(UTC, "UTC",
                                                                                                     "pasted")
    day = parse.resolve_days(request, deps.now.astimezone(zone.tz).date())[0]
    raws = paste.parse(query, day, zone.tz)
    placed = ev.place(raws, (day,), zone.tz)
    plans = dayplan.build_days(placed, (day,), zone.tz, hours=hours, part=request.part, min_gap=request.min_gap)
    plan = Plan(days=plans, zone={**zone.to_dict(), "name": zone.name}, pasted=True,
                coverage=("calendar: not read; this plan uses only the meetings you pasted.",))
    return Outcome(render.render_plan(plan, zone.tz), plan, envelope.build(plan, zone.tz))


async def _unavailable(deps: TurnDeps, query: str, line: str) -> Outcome:
    """No calendar to read: one tool-less scope check, so an off-topic ask is still declined."""
    verdict = await ask_once(deps, [{"role": "system", "content": prompts.scope_system()},
                                    {"role": "user", "content": query}])
    log.info("scope check: %s", verdict[:40])
    if prompts.OFF_TOPIC in verdict.upper() and prompts.IN_SCOPE not in verdict.upper():
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    return Outcome(f"I couldn't read your calendar, so I won't guess.\n\nCoverage: {line}\n\n{prompts.PASTE_PROMPT}")


def _coverage(dropped: dict) -> tuple[str, ...]:
    if not dropped:
        return ()
    reasons = ", ".join(dict.fromkeys(dropped.values()))
    return (f"calendar: some calendar reads failed ({reasons}); this plan may be partial.",)


def _tz_question(header: str) -> dict:
    return nasiko_hitl.question_metadata(header, [(label, "") for label in prompts.TZ_OPTIONS],
                                         allow_custom_input=True)


def _fmt(hours: tuple[time, time]) -> tuple[str, str]:
    return hours[0].strftime("%H:%M"), hours[1].strftime("%H:%M")
