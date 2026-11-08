"""Pure: SLA targets and breaches, in calendar minutes from ticket timestamps and the turn's fixed clock.

Targets come from the desk's policies when they were read (a priority the desk does not define falls back
to the default for that priority), else from the defaults in config.py; the result says which. A time
that cannot be known is counted as unknown, never as a breach."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from config import (
    AWAITING_REPLY_STATUSES,
    FIRST_REPLY_MINUTES,
    OPEN_STATUSES,
    RESOLUTION_MINUTES,
    norm_priority,
)
from records import Breach, SlaPolicy, Ticket, parse_time

WORST_N = 5
AT_RISK_SHARE = 0.8   # an open ticket with no reply past this share of its target is "at risk soon"


@dataclass(frozen=True)
class Targets:
    first_reply: dict[str, int]
    resolution: dict[str, int]
    source: str                          # "desk" | "defaults"
    defaulted: tuple[str, ...] = ()      # priorities filled from defaults when the source is the desk


def targets(policies: Sequence[SlaPolicy]) -> Targets:
    if not policies:
        return Targets(dict(FIRST_REPLY_MINUTES), dict(RESOLUTION_MINUTES), "defaults")
    first, resolution = dict(FIRST_REPLY_MINUTES), dict(RESOLUTION_MINUTES)
    covered: set[str] = set()
    for p in policies:
        if p.first_reply_minutes is not None:
            first[p.priority] = p.first_reply_minutes
            covered.add(p.priority)
        if p.resolution_minutes is not None:
            resolution[p.priority] = p.resolution_minutes
            covered.add(p.priority)
    missing = tuple(k for k in FIRST_REPLY_MINUTES if k not in covered)
    return Targets(first, resolution, "desk", missing)


@dataclass(frozen=True)
class SlaResult:
    first_reply_breaches: int
    resolution_breaches: int
    open_breaches: int
    at_risk_soon: int
    unknown_times: int
    first_reply_known: int
    breaches: tuple[Breach, ...]

    @property
    def first_reply_rate(self) -> float | None:
        if not self.first_reply_known:
            return None
        return round(self.first_reply_breaches / self.first_reply_known * 100, 1)


def _minutes(start: str, end: str) -> int | None:
    a, b = parse_time(start), parse_time(end)
    return int((b - a).total_seconds() // 60) if a and b and b >= a else None


def _elapsed(ticket: Ticket, now: datetime) -> int | None:
    created = parse_time(ticket.created_at)
    return int((now - created).total_seconds() // 60) if created and now >= created else None


def _is_open(ticket: Ticket) -> bool:
    return ticket.status.casefold() in OPEN_STATUSES


def _breach(t: Ticket, kind: str, is_open: bool, overrun: int) -> Breach:
    return Breach(ticket_id=t.id, kind=kind, open=is_open, overrun_minutes=overrun,
                  priority=norm_priority(t.priority), customer=t.organization, url=t.url, subject=t.subject)


def first_reply(t: Ticket, target: int, now: datetime) -> tuple[str, Breach | None]:
    """("met" | "breached" | "at_risk" | "waiting" | "unknown", breach)."""
    actual = t.reply_minutes if t.reply_minutes is not None else _minutes(t.created_at, t.first_reply_at)
    if actual is not None:
        return ("breached", _breach(t, "first_reply", False, actual - target)) if actual > target else ("met", None)
    awaiting = t.no_reply or t.status.casefold() in AWAITING_REPLY_STATUSES
    elapsed = _elapsed(t, now)
    if not awaiting or elapsed is None:
        return "unknown", None
    if elapsed > target:
        return "breached", _breach(t, "first_reply", True, elapsed - target)
    return ("at_risk", None) if elapsed >= target * AT_RISK_SHARE else ("waiting", None)


def resolution(t: Ticket, target: int, now: datetime) -> tuple[str, Breach | None]:
    actual = t.resolve_minutes if t.resolve_minutes is not None else _minutes(t.created_at, t.solved_at)
    if actual is not None:
        return ("breached", _breach(t, "resolution", False, actual - target)) if actual > target else ("met", None)
    elapsed = _elapsed(t, now)
    if not _is_open(t) or elapsed is None:
        return "unknown", None
    return ("breached", _breach(t, "resolution", True, elapsed - target)) if elapsed > target else ("waiting", None)



def assess(tickets: Sequence[Ticket], tg: Targets, now: datetime) -> SlaResult:
    breaches: list[Breach] = []
    counts = {"first": 0, "resolution": 0, "at_risk": 0, "unknown": 0, "first_known": 0}
    for t in tickets:
        p = norm_priority(t.priority)
        fr_state, fr = first_reply(t, tg.first_reply[p], now)
        rs_state, rs = resolution(t, tg.resolution[p], now)
        counts["first"] += fr is not None
        counts["resolution"] += rs is not None
        counts["at_risk"] += fr_state == "at_risk"
        counts["unknown"] += "unknown" in (fr_state, rs_state)
        counts["first_known"] += fr_state != "unknown"
        breaches += [b for b in (fr, rs) if b is not None]
    return SlaResult(first_reply_breaches=counts["first"], resolution_breaches=counts["resolution"],
                     open_breaches=sum(b.open for b in breaches), at_risk_soon=counts["at_risk"],
                     unknown_times=counts["unknown"], first_reply_known=counts["first_known"],
                     breaches=tuple(ranked(breaches)))


def ranked(breaches: Sequence[Breach]) -> list[Breach]:
    """Largest overrun first; ties by ticket id, then first reply before resolution."""
    return sorted(breaches, key=lambda b: (-b.overrun_minutes, b.ticket_id, b.kind != "first_reply"))


def worst(result: SlaResult, n: int = WORST_N) -> tuple[Breach, ...]:
    return result.breaches[:n]


def overrun_hours(b: Breach) -> float:
    return round(b.overrun_minutes / 60, 1)
