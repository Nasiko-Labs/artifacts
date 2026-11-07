"""Pure: one DayPlan per requested day, from the placed events. Every number here is computed in code."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, time, tzinfo

import events as ev
import flags
import gaps as g
from models import DayPlan, DayStats, Event


def build_days(placed: ev.Sorted, days: Sequence[date], tz: tzinfo, *, hours: tuple[time, time],
               part: tuple[time, time] | None, min_gap: int) -> tuple[DayPlan, ...]:
    plans = []
    for i, day in enumerate(days):
        timed = tuple(e for e in placed.timed if e.start.astimezone(tz).date() == day)
        all_day = tuple(a for a in placed.all_day if ev.covers(a, {day}))
        declined = placed.declined if i == 0 else 0  # counted once, on the first day shown
        plans.append(build_day(day, timed, all_day, tz, hours=hours, part=part, min_gap=min_gap, declined=declined))
    return tuple(plans)


def build_day(day: date, timed: Sequence[Event], all_day, tz: tzinfo, *, hours: tuple[time, time],
              part: tuple[time, time] | None, min_gap: int, declined: int = 0) -> DayPlan:
    events = flags.flagged(timed, tz)
    start, end = g.hours_window(day, hours, part, tz)
    all_free = g.free(events, start, end)
    shown = g.at_least(all_free, min_gap)
    longest = g.best(shown)
    stats = DayStats(
        meetings=len(events),
        meeting_minutes=g.clipped_minutes(g.busy(events), start, end),
        free_minutes=sum(gap.minutes for gap in all_free),
        longest_gap_min=longest.minutes if longest else 0,
        back_to_back_runs=g.back_to_back_runs(events),
        double_bookings=len(g.overlaps(events)),
        declined_dropped=declined,
    )
    return DayPlan(day=day, events=events, all_day=tuple(all_day), gaps=shown, best=longest, stats=stats,
                   hours=(hours[0].strftime("%H:%M"), hours[1].strftime("%H:%M")))


def with_context(plan: DayPlan, contexts: dict[str, tuple[str | None, str | None]]) -> DayPlan:
    """The same day with each event's verified context line (keyed by event key)."""
    updated = tuple(replace(e, context=contexts.get(key_of(e), (None, None))[0],
                            evidence=contexts.get(key_of(e), (None, None))[1]) for e in plan.events)
    return replace(plan, events=updated)


def key_of(event: Event) -> str:
    return f"{event.raw.id}|{event.start.isoformat()}"
