"""Pure: windows, baselines and per-day means over parsed rows. Money stays in integer micro-units; means and
deltas are exact `Fraction`s, so threshold boundaries (0.19 vs 0.20, 499.99 vs 500.00) compare exactly."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from export import Row

BASELINE_DAYS = 28
DEFAULT_WINDOW_DAYS = 7
MIN_WINDOW_DAYS = 3
MAX_WINDOW_DAYS = 14


@dataclass(frozen=True)
class Periods:
    window: tuple[date, ...]
    baseline: tuple[date, ...]

    @property
    def all_days(self) -> tuple[date, ...]:
        return (*self.baseline, *self.window)


def clamp_window(days: int | None) -> int:
    if days is None:
        return DEFAULT_WINDOW_DAYS
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, days))


def day_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range((end - start).days + 1)) if end >= start else ()


def periods(rows: Sequence[Row], window_days: int) -> Periods:
    """W = the last `window_days` days of data; B = up to 28 days before W, never before the first day."""
    first = min(r.day for r in rows)
    last = max(r.day for r in rows)
    w_start = max(first, last - timedelta(days=window_days - 1))
    b_start = max(first, w_start - timedelta(days=BASELINE_DAYS))
    return Periods(day_range(w_start, last), day_range(b_start, w_start - timedelta(days=1)))


def daily(rows: Iterable[Row], days: Sequence[date]) -> tuple[int, ...]:
    """Cost per day for `days` (a day with no rows costs 0)."""
    totals: dict[date, int] = {}
    for r in rows:  # a local accumulator; the input is never changed
        totals[r.day] = totals.get(r.day, 0) + r.cost_micros
    return tuple(totals.get(d, 0) for d in days)


def mean_over(rows: Iterable[Row], days: Sequence[date]) -> Fraction:
    if not days:
        return Fraction(0)
    wanted = set(days)
    return Fraction(sum(r.cost_micros for r in rows if r.day in wanted), len(days))


def delta(rows: Sequence[Row], p: Periods) -> Fraction:
    return mean_over(rows, p.window) - mean_over(rows, p.baseline)


def group(rows: Iterable[Row], key: Callable[[Row], str]) -> dict[str, tuple[Row, ...]]:
    out: dict[str, list[Row]] = {}
    for r in rows:  # a local accumulator; the input is never changed
        out.setdefault(key(r), []).append(r)
    return {k: tuple(v) for k, v in out.items()}


def main_currency(rows: Sequence[Row]) -> str:
    """The currency with the largest absolute total; amounts are never converted."""
    by = group(rows, lambda r: r.currency)
    return max(sorted(by), key=lambda c: abs(sum(r.cost_micros for r in by[c])))
