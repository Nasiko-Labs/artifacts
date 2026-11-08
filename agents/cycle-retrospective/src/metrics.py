"""Pure: every number in the retro. Cycle window `[starts_at, ends_at]` (a running cycle ends today).

- planned: added to the cycle (or, without that field, created) by start + the grace hours; the rest is
  added mid-cycle;
- completed: a completion time inside the window; canceled: canceled issues, counted apart and nowhere else;
- spilled: neither completed nor canceled at the end of the window ("open so far" while it runs)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from dates import hours, instant
from records import Cycle, Issue

_LATEST = datetime.max.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Metrics:
    planned_count: int = 0
    completed_count: int = 0
    completed_planned_count: int = 0
    added_count: int = 0
    spilled_count: int = 0
    canceled_count: int = 0
    completion_rate: float | None = None     # completed planned / planned, percent
    scope_added_pct: float | None = None     # added / planned, percent
    points_planned: float = 0.0
    points_completed: float = 0.0
    points_spilled: float = 0.0
    carry_over_points: float = 0.0
    unestimated: int = 0
    created_fallback: int = 0                # issues placed by created_at (no added-to-cycle time)
    in_progress: bool = False


@dataclass(frozen=True)
class Split:
    planned: tuple[Issue, ...]
    added: tuple[Issue, ...]
    completed: tuple[Issue, ...]
    spilled: tuple[Issue, ...]
    canceled: tuple[Issue, ...]
    metrics: Metrics


def in_progress(cycle: Cycle, now: datetime) -> bool:
    start, end = instant(cycle.starts_at), instant(cycle.ends_at, end_of_day=True)
    return bool(start and end and start <= now <= end)


def split(issues: Sequence[Issue], cycle: Cycle, now: datetime, grace_hours: int) -> Split:
    start = instant(cycle.starts_at)
    running = in_progress(cycle, now)
    end = now if running else instant(cycle.ends_at, end_of_day=True)
    cutoff = start + hours(grace_hours) if start else None
    live = [i for i in issues if not i.canceled]
    canceled = tuple(i for i in issues if i.canceled)
    planned = tuple(i for i in live if _is_planned(i, cutoff))
    added = tuple(i for i in live if i not in planned)
    completed = tuple(i for i in live if _completed(i, end))
    spilled = tuple(i for i in live if i not in completed)
    fallback = sum(1 for i in live if not instant(i.added_to_cycle_at))
    done_planned = sum(1 for i in planned if i in completed)
    metrics = Metrics(
        planned_count=len(planned), completed_count=len(completed), completed_planned_count=done_planned,
        added_count=len(added), spilled_count=len(spilled), canceled_count=len(canceled),
        completion_rate=percent(done_planned, len(planned)), scope_added_pct=percent(len(added), len(planned)),
        points_planned=points(planned), points_completed=points(completed), points_spilled=points(spilled),
        carry_over_points=points(spilled), unestimated=sum(1 for i in live if i.estimate is None),
        created_fallback=fallback, in_progress=running)
    return Split(planned, added, shipped_order(completed), spill_order(spilled), canceled, metrics)


def _is_planned(issue: Issue, cutoff: datetime | None) -> bool:
    joined = instant(issue.added_to_cycle_at) or instant(issue.created_at)
    return bool(cutoff and joined and joined <= cutoff)


def _completed(issue: Issue, end: datetime | None) -> bool:
    done = instant(issue.completed_at)
    return bool(done and end and done <= end)


def percent(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return float((Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def points(issues: Sequence[Issue]) -> float:
    return float(sum(i.estimate for i in issues if i.estimate is not None))


def shipped_order(issues: Sequence[Issue]) -> tuple[Issue, ...]:
    """Estimate (largest first, unestimated last), then completion time."""
    return tuple(sorted(issues, key=lambda i: (-(i.estimate if i.estimate is not None else -1),
                                               instant(i.completed_at) or _LATEST,
                                               i.id)))


def spill_order(issues: Sequence[Issue]) -> tuple[Issue, ...]:
    return tuple(sorted(issues, key=lambda i: (-(i.estimate if i.estimate is not None else -1), i.id)))


def fmt_points(value: float | None) -> str:
    if value is None:
        return "?"
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"
