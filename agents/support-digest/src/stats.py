"""Pure: volume, trend, busiest day, top customers and backlog, all computed in code from ticket timestamps
(UTC calendar days of the period). The model never counts, compares or sorts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from config import OPEN_STATUSES
from period import Period
from records import Ticket, parse_time

TREND_THRESHOLD_PCT = 10.0


@dataclass(frozen=True)
class Volume:
    total: int
    previous_total: int | None
    change_pct: float | None
    direction: str                         # up | down | flat | n/a
    by_day: tuple[tuple[str, int], ...]
    busiest: tuple[str, int] | None

    def to_dict(self) -> dict:
        return {"total": self.total, "previous_total": self.previous_total,
                "change_pct": self.change_pct if self.change_pct is not None else "n/a",
                "direction": self.direction, "by_day": [{"date": d, "count": n} for d, n in self.by_day]}


def created_day(ticket: Ticket) -> date | None:
    when = parse_time(ticket.created_at)
    return when.date() if when else None


def in_period(tickets: Sequence[Ticket], p: Period) -> tuple[Ticket, ...]:
    return tuple(t for t in tickets if (d := created_day(t)) is not None and p.holds(d))


def by_day(tickets: Sequence[Ticket], p: Period) -> tuple[tuple[str, int], ...]:
    counts = Counter(created_day(t) for t in tickets)
    days = (p.start + timedelta(days=i) for i in range(p.days))
    return tuple((d.isoformat(), counts.get(d, 0)) for d in days)


def change_pct(total: int, previous_total: int | None) -> float | None:
    if not previous_total:
        return None
    return round((total - previous_total) / previous_total * 100, 1)


def direction(change: float | None, threshold: float = TREND_THRESHOLD_PCT) -> str:
    if change is None:
        return "n/a"
    if change >= threshold:
        return "up"
    return "down" if change <= -threshold else "flat"


def busiest(days: Sequence[tuple[str, int]]) -> tuple[str, int] | None:
    """The day with the most tickets; ties go to the earliest. None when no day has a ticket."""
    best: tuple[str, int] | None = None
    for day, n in days:
        if n and (best is None or n > best[1]):
            best = (day, n)
    return best


def volume(tickets: Sequence[Ticket], p: Period, previous_total: int | None, reported_total: int | None = None
           ) -> Volume:
    """`reported_total` (the desk's own count, when larger than the sample) replaces the sampled count."""
    days = by_day(tickets, p)
    total = reported_total if reported_total is not None and reported_total > len(tickets) else len(tickets)
    change = change_pct(total, previous_total)
    return Volume(total=total, previous_total=previous_total, change_pct=change, direction=direction(change),
                  by_day=days, busiest=busiest(days))


def is_open(ticket: Ticket) -> bool:
    return ticket.status.casefold() in OPEN_STATUSES


def top_customers(tickets: Sequence[Ticket], n: int) -> tuple[dict, ...]:
    """By ticket count, ties alphabetical; tickets with no (verified) organization are not counted."""
    totals = Counter(t.organization for t in tickets if t.organization)
    opened = Counter(t.organization for t in tickets if t.organization and is_open(t))
    order = sorted(totals, key=lambda name: (-totals[name], name.casefold()))
    return tuple({"name": name, "tickets": totals[name], "open": opened.get(name, 0)} for name in order[:n])


def backlog(tickets: Sequence[Ticket]) -> int:
    return sum(is_open(t) for t in tickets)
