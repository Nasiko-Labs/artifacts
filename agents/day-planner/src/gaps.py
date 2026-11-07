"""Pure: free gaps, double bookings and the day's numbers, computed in code.

Busy time is the union of the day's timed events; free time is the working hours (optionally narrowed
to the part of the day the user asked about) minus busy time. All-day events never block time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

import timewin
from models import Event, Gap

BACK_TO_BACK = timedelta(minutes=5)


@dataclass(frozen=True)
class Overlap:
    first: Event
    second: Event


def hours_window(day: date, hours: tuple[time, time], part: tuple[time, time] | None,
                 tz: tzinfo) -> tuple[datetime, datetime]:
    start, end = hours
    if part is not None:
        start, end = max(start, part[0]), min(end, part[1])
    if end <= start:
        return timewin.at_local(day, start, tz), timewin.at_local(day, start, tz)
    return timewin.at_local(day, start, tz), _end_of(day, end, tz)


def _end_of(day: date, clock: time, tz: tzinfo) -> datetime:
    if clock == time.max:
        return timewin.at_local(day + timedelta(days=1), time.min, tz)
    return timewin.at_local(day, clock, tz)


def busy(events: Sequence[Event]) -> tuple[tuple[datetime, datetime], ...]:
    """Merged busy intervals, in order."""
    merged: list[tuple[datetime, datetime]] = []
    for event in sorted(events, key=lambda e: e.start):
        if merged and event.start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], event.end))
        else:
            merged.append((event.start, event.end))
    return tuple(merged)


def free(events: Sequence[Event], start: datetime, end: datetime) -> tuple[Gap, ...]:
    """Every free interval inside [start, end), with the events on either side."""
    gaps: list[Gap] = []
    cursor = start
    for b_start, b_end in busy(events):
        if b_start > cursor and cursor < end:
            gaps.append(Gap(cursor, min(b_start, end)))
        cursor = max(cursor, b_end)
    if cursor < end:
        gaps.append(Gap(cursor, end))
    return tuple(_named(g, events) for g in gaps if g.end > g.start)


def _named(gap: Gap, events: Sequence[Event]) -> Gap:
    after = next((e.raw.id for e in sorted(events, key=lambda e: e.end, reverse=True) if e.end <= gap.start), "")
    before = next((e.raw.id for e in sorted(events, key=lambda e: e.start) if e.start >= gap.end), "")
    return Gap(gap.start, gap.end, after_event_id=after, before_event_id=before)


def at_least(gaps: Sequence[Gap], minutes: int) -> tuple[Gap, ...]:
    return tuple(g for g in gaps if g.minutes >= minutes)


def best(gaps: Sequence[Gap]) -> Gap | None:
    """The longest gap; ties go to the earlier one."""
    return max(gaps, key=lambda g: (g.minutes, -g.start.timestamp()), default=None)


def overlaps(events: Sequence[Event]) -> tuple[Overlap, ...]:
    ordered = sorted(events, key=lambda e: (e.start, e.end))
    return tuple(Overlap(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1:]
                 if b.start < a.end and a.end > a.start and b.end > b.start)


def back_to_back_runs(events: Sequence[Event]) -> int:
    """Chains of two or more meetings each starting less than five minutes after the chain's end."""
    runs, length, chain_end = 0, 0, None
    for event in sorted(events, key=lambda e: e.start):
        if chain_end is not None and event.start < chain_end + BACK_TO_BACK:
            length += 1
            chain_end = max(chain_end, event.end)
            continue
        runs += 1 if length >= 2 else 0
        length, chain_end = 1, event.end
    return runs + (1 if length >= 2 else 0)


def clipped_minutes(intervals: Sequence[tuple[datetime, datetime]], start: datetime, end: datetime) -> int:
    total = sum(max(timedelta(0), min(b, end) - max(a, start)).total_seconds() for a, b in intervals)
    return int(total // 60)
