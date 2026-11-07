"""Pure: is there a spend spike in the window, how big, and when did it start.

Daily total D(d). W = last N days of data, B = the 28 days before W.
- base_daily = mean(D over B); win_daily = mean(D over W); delta_daily = win - base; delta_pct = delta / base.
- Spike when delta_pct >= 0.20 and delta_daily × 30 >= 500.00, or delta_pct >= 0.50 and delta_daily × 30 >= 100.00.
  With a zero baseline (new spend) delta_pct is n/a and the 500.00 floor alone decides.
- Onset: the first day (from the last 3 days of B, then W) with D >= base + 2·sd_B that stays above base + sd_B on
  at least 2 of the next 3 days (or every remaining day, near the window end). A 2-sigma day that does not stay up
  is a one-day jump (onset flagged, not sustained). Neither → onset = first day of W, "gradual rise".
- Fewer than 14 baseline days → not enough history: top movers by first-half vs second-half, no spike asserted.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal

import aggregate as agg
from export import MICROS, Row

SPIKE_PCT = Fraction(20, 100)
BIG_PCT = Fraction(50, 100)
IMPACT_FLOOR = 500 * MICROS
BIG_IMPACT_FLOOR = 100 * MICROS
MONTH_DAYS = 30
MIN_BASELINE_DAYS = 14
ONSET_LOOKBACK_DAYS = 3
ONSET_FOLLOW_DAYS = 3
ONSET_FOLLOW_NEEDED = 2
TOP_MOVERS = 3

OnsetKind = Literal["step", "one_day", "gradual"]


@dataclass(frozen=True)
class Onset:
    day: date
    kind: OnsetKind


@dataclass(frozen=True)
class Spike:
    periods: agg.Periods
    base_daily: Fraction
    win_daily: Fraction
    delta_pct: Fraction | None
    is_spike: bool
    onset: Onset
    budget_micros: int | None = None

    @property
    def delta_daily(self) -> Fraction:
        return self.win_daily - self.base_daily

    @property
    def monthly_impact(self) -> Fraction:
        return self.delta_daily * MONTH_DAYS

    @property
    def projected_month(self) -> Fraction:
        return self.win_daily * MONTH_DAYS

    @property
    def over_budget(self) -> bool | None:
        return None if self.budget_micros is None else self.projected_month > self.budget_micros


@dataclass(frozen=True)
class Mover:
    service: str
    delta_daily: Fraction


@dataclass(frozen=True)
class ShortHistory:
    baseline_days: int
    movers: tuple[Mover, ...]


def is_spike(delta_daily: Fraction, delta_pct: Fraction | None) -> bool:
    impact = delta_daily * MONTH_DAYS
    if delta_daily <= 0:
        return False
    if delta_pct is None:
        return impact >= IMPACT_FLOOR
    return (delta_pct >= SPIKE_PCT and impact >= IMPACT_FLOOR) or (delta_pct >= BIG_PCT and impact >= BIG_IMPACT_FLOOR)


def detect(rows: Sequence[Row], window_days: int, budget_micros: int | None = None) -> Spike | ShortHistory:
    p = agg.periods(rows, window_days)
    if len(p.baseline) < MIN_BASELINE_DAYS:
        return ShortHistory(len(p.baseline), halves_movers(rows))
    base = agg.mean_over(rows, p.baseline)
    win = agg.mean_over(rows, p.window)
    pct = (win - base) / base if base > 0 else None
    onset = find_onset(agg.daily(rows, p.all_days), p, base)
    return Spike(p, base, win, pct, is_spike(win - base, pct), onset, budget_micros)


def _sd(values: Sequence[int], mean: Fraction) -> float:
    return math.sqrt(float(sum((Fraction(v) - mean) ** 2 for v in values) / len(values))) if values else 0.0


def find_onset(series: Sequence[int], p: agg.Periods, base: Fraction) -> Onset:
    """`series` is D over B then W (same order as `p.all_days`)."""
    days = p.all_days
    sd = _sd(series[: len(p.baseline)], base)
    high = float(base) + 2 * sd
    above = float(base) + sd
    start = max(0, len(p.baseline) - ONSET_LOOKBACK_DAYS)
    first_jump: int | None = None
    for i in range(start, len(days)):
        value = series[i]
        if value <= base or value < high:
            continue
        following = series[i + 1: i + 1 + ONSET_FOLLOW_DAYS]
        needed = min(ONSET_FOLLOW_NEEDED, len(following))
        if following and sum(1 for v in following if v > above) >= needed:
            return Onset(days[i], "step")
        first_jump = i if first_jump is None else first_jump
    if first_jump is not None:
        return Onset(days[first_jump], "one_day")
    return Onset(p.window[0], "gradual")


def halves_movers(rows: Sequence[Row]) -> tuple[Mover, ...]:
    """Short history: per-service mean of the second half of the days minus the first half."""
    days = agg.day_range(min(r.day for r in rows), max(r.day for r in rows))
    half = len(days) // 2
    if half < 1:
        return ()
    p = agg.Periods(window=days[half:], baseline=days[:half])
    return top_movers(rows, p)


def top_movers(rows: Sequence[Row], p: agg.Periods) -> tuple[Mover, ...]:
    by = agg.group(rows, lambda r: r.service)
    movers = [Mover(s, agg.delta(rs, p)) for s, rs in sorted(by.items())]
    ranked = sorted((m for m in movers if m.delta_daily > 0), key=lambda m: -m.delta_daily)
    return tuple(ranked[:TOP_MOVERS])
