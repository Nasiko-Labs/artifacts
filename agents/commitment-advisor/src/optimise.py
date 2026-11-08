"""Pure: the commitment sizing maths on a residual hourly series R_t ($/hr, on-demand equivalent).

For a level X: covered_t = min(R_t, X); utilization = sum(covered) / (X * T); net saving over the span =
sum(covered) - X * (1 - d) * T. Break-even utilization is 1 - d. A level is eligible when utilization >=
the minimum and the saving is positive; X* is the eligible grid level with the largest saving."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from constants import (
    DAILY_SAFETY,
    EPS,
    GRID_PERCENTILES,
    HOURS_PER_MONTH,
    MONTHS_PER_YEAR,
)


@dataclass(frozen=True)
class Level:
    x: float
    utilization: float
    net_saving: float


def percentile(values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (numpy's default), with no dependency."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of an empty series")
    pos = (len(ordered) - 1) * q / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def grid(r: Sequence[float]) -> tuple[float, ...]:
    return tuple(sorted({0.0, *(round(percentile(r, p), 6) for p in GRID_PERCENTILES)}))


def evaluate(r: Sequence[float], x: float, d: float) -> Level:
    covered = sum(min(v, x) for v in r)
    hours = len(r)
    utilization = covered / (x * hours) if x > 0 and hours else 0.0
    return Level(x, utilization, covered - x * (1 - d) * hours)


def is_eligible(level: Level, min_utilization: float) -> bool:
    return level.x > 0 and level.utilization >= min_utilization - EPS and level.net_saving > EPS


def best(r: Sequence[float], d: float, min_utilization: float) -> tuple[Level | None, Level | None]:
    """(X*, the highest-utilization positive level for reference)."""
    levels = [evaluate(r, x, d) for x in grid(r) if x > 0]
    eligible = [lv for lv in levels if is_eligible(lv, min_utilization)]
    chosen = max(eligible, key=lambda lv: (lv.net_saving, lv.x)) if eligible else None
    reference = max(levels, key=lambda lv: (lv.utilization, lv.x)) if levels else None
    return chosen, reference


def monthly(net_saving: float, hours: int) -> float:
    return net_saving / hours * HOURS_PER_MONTH


def size(r: Sequence[float], d: float, years: int, daily: bool, min_utilization: float) -> dict:
    """The rounded, safety-adjusted commitment for one term, or why there is none."""
    chosen, reference = best(r, d, min_utilization)
    base = {"term_years": years, "discount": d, "break_even_utilization": round(1 - d, 4)}
    commit = round(chosen.x * (1 - d) * (DAILY_SAFETY if daily else 1.0), 2) if chosen else 0.0
    if commit <= 0:
        ref = {"reference_od_hourly": round(reference.x, 2), "reference_utilization": round(reference.utilization, 4)
               } if reference else {}
        return {**base, "eligible": False, **ref}
    x_adj = commit / (1 - d)
    level = evaluate(r, x_adj, d)
    month = monthly(level.net_saving, len(r))
    return {**base, "eligible": True, "hourly_commit": commit, "od_hourly": round(x_adj, 4),
            "utilization": round(level.utilization, 4), "net_saving": round(level.net_saving, 2),
            "monthly_saving": round(month, 2), "term_saving": round(month * MONTHS_PER_YEAR * years, 2)}
