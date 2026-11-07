"""Meeting-card mode, one turn:

    cached cards in this conversation? ─┬─ answer / OFF_TOPIC ─────────────────────────→ reply
                                        ├─ NEEDS_REFRESH:<n> → context + card for #n ───→ reply
                                        └─ NEW_PREP (or no cache) ─┐
    parse day / tz / focus (code) → discover calendar tools ─ none → fixed reply (0 LLM calls)
    calendar step → verified events → timezone, local day, filters, cap 5 (code)
    discover context tools once → per meeting, at most 3 at a time: context step → card step
    render cards + coverage notes (code) → cache for follow-ups

No meeting starts its context step after CONTEXT_START_DEADLINE_SECS; those are listed instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

import coverage
import events as ev
import followup
import prep_prompts as prompts
import render_cards
import smalltalk
import timewin
from cache import PrepCache
from calendar_step import read_calendar
from context_step import Prepared, prepare
from deps import TurnDeps
from discovery import RECOVER_META, classify, discover
from nasiko_mcp import (
    GatewayAuthError,
    GatewayForbiddenError,
    McpTool,
    MissingTraceparentError,
)
from tool_health import ToolHealth

log = logging.getLogger("meeting_prep.prep")

CONCURRENCY = 3
CONTEXT_START_DEADLINE_SECS = 360.0
_FATAL = (GatewayAuthError, GatewayForbiddenError, MissingTraceparentError)
_HELPERS = frozenset({RECOVER_META})


@dataclass(frozen=True)
class _Outcome:
    meeting: ev.Meeting
    prepared: Prepared | None = None
    late: bool = False


@dataclass(frozen=True)
class _ContextTools:
    tools: tuple[McpTool, ...] = ()
    source_of: Callable[[McpTool], str | None] = classify


async def handle(deps: TurnDeps, query: str, context_id: str, cache: PrepCache) -> str:
    """A follow-up when this conversation already has cards, else a fresh prep."""
    if not deps.traceparent:
        return prompts.NO_TRACE_CONTEXT
    state = await cache.load(context_id)
    rendered = render_cards.Rendered.from_dict(state["rendered"]) if state and state.get("rendered") else None
    if rendered is not None:
        decision = await followup.decide(deps, query, rendered.text)
        if decision.kind == "off_topic":
            return smalltalk.OFF_TOPIC_REPLY
        if decision.kind == "answer":
            return decision.text
        if decision.kind == "refresh":
            return await refresh(deps, state, decision.number, context_id, cache)
    return await prep_day(deps, query, context_id, cache)


async def prep_day(deps: TurnDeps, query: str, context_id: str, cache: PrepCache) -> str:
    started = deps.monotonic()
    request = timewin.parse_request(query, deps.now)
    catalog, failure = await discover(deps, "calendar")
    if failure is not None:
        return failure
    health = ToolHealth(deps.gateway)
    calendar = await read_calendar(deps, catalog, health, query, timewin.read_window(deps.now, request))
    if calendar.reply is not None:
        return calendar.reply
    zone = timewin.resolve_zone(stated=request.stated_tz, calendar_tz=calendar.calendar_tz,
                                calendar_tz_seen=calendar.calendar_tz_seen,
                                event_starts=[e.start for e in calendar.events], default_tz=deps.default_tz)
    day = timewin.target_date(deps.now, zone.tz, request.offset_days)
    selection = ev.select(calendar.events, me=calendar.me, tz=zone.tz, day=timewin.local_day(day, zone.tz),
                          now=deps.now, include_ended=request.include_ended, focus=request.focus, total=calendar.total)
    if not selection.cards:
        return _nothing(day, zone, request)
    context = await _context_tools(deps)
    outcomes = await _prepare_all(deps, context.tools, health, selection.cards, calendar.me, zone, started)
    report = _report(day, zone, selection, outcomes, context, health)
    rendered = render_cards.render(report)
    await cache.save(context_id, _state(report, calendar.me, rendered))
    return rendered.text


def _nothing(day: date, zone: timewin.Zone, request: timewin.DayRequest) -> str:
    label = timewin.day_label(day)
    if request.focus is not None:
        return (f"I found no meeting with other people at {request.focus.strftime('%H:%M')} on {label} "
                f"(times in {zone.name}).")
    return prompts.no_meetings(label, zone.name, request.include_ended)


async def _context_tools(deps: TurnDeps) -> _ContextTools:
    catalog, failure = await discover(deps, "context")
    if failure is not None:
        log.warning("context discovery failed; cards come from the invite only")
        return _ContextTools()
    tools = catalog.read_tools()
    log.info("context tools offered: %s", [t.name for t in tools])
    return _ContextTools(tools, catalog.source_of)


async def _prepare_all(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth,
                       meetings: Sequence[ev.Meeting], me: str, zone: timewin.Zone,
                       started: float) -> tuple[_Outcome, ...]:
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(meeting: ev.Meeting) -> _Outcome:
        async with gate:
            if deps.monotonic() - started > CONTEXT_START_DEADLINE_SECS:
                return _Outcome(meeting, late=True)
            try:
                return _Outcome(meeting, await prepare(deps, tools, health, meeting, me=me, tz=zone.tz))
            except _FATAL:
                raise
            except Exception:  # one meeting failing must not lose the others
                log.exception("preparing meeting %s failed", meeting.event.id)
                return _Outcome(meeting)

    return tuple(await asyncio.gather(*(one(m) for m in meetings)))


def _notes(context: _ContextTools, health: ToolHealth, links_seen: bool) -> tuple[str, ...]:
    statuses = coverage.assess(context.tools, context.source_of, health.dropped, helpers=_HELPERS)
    return coverage.notes(statuses, links_seen=links_seen)


def _report(day: date, zone: timewin.Zone, selection: ev.Selection, outcomes: Sequence[_Outcome],
            context: _ContextTools, health: ToolHealth) -> render_cards.Report:
    prepared = tuple(o.prepared for o in outcomes if o.prepared is not None)
    return render_cards.Report(
        day=day, zone=zone, prepared=prepared, overflow=selection.overflow,
        not_in_time=tuple(o.meeting for o in outcomes if o.late),
        failed=tuple(o.meeting for o in outcomes if o.prepared is None and not o.late),
        notes=_notes(context, health, any(m.event.links for m in selection.cards)),
        dropped=selection.dropped + sum(p.dropped for p in prepared),
    )


def _state(report: render_cards.Report, me: str, rendered: render_cards.Rendered) -> dict:
    return {"day": report.day.isoformat(), "zone": report.zone.name, "zone_source": report.zone.source,
            "offset_minutes": timewin.offset_minutes(report.zone), "me": me,
            "prepared": [p.to_dict() for p in report.prepared], "rendered": rendered.to_dict()}


async def refresh(deps: TurnDeps, state: dict, number: int, context_id: str, cache: PrepCache) -> str:
    """Re-run the context and card steps for one cached meeting only."""
    prepared = [Prepared.from_dict(p) for p in state.get("prepared") or ()]
    if not 1 <= number <= len(prepared):
        return f"There is no meeting #{number} in the cards I gave you."
    zone = timewin.restore_zone(state.get("zone") or "UTC", state.get("zone_source") or "assumed",
                                state.get("offset_minutes"))
    health = ToolHealth(deps.gateway)
    context = await _context_tools(deps)
    fresh = await prepare(deps, context.tools, health, prepared[number - 1].meeting, me=state.get("me") or "",
                          tz=zone.tz)
    card_text = render_cards.render_card(number, fresh, zone.tz)
    updated = [*prepared[:number - 1], fresh, *prepared[number:]]
    rendered = render_cards.Rendered.from_dict(state["rendered"]).with_card(number, card_text)
    await cache.save(context_id, {**state, "prepared": [p.to_dict() for p in updated], "rendered": rendered.to_dict()})
    notes = _notes(context, health, bool(fresh.meeting.event.links))
    return "\n\n".join([card_text, *notes, f"{prompts.READ_ONLY_FOOTER} {prompts.DRAFTER_HINT}"])
