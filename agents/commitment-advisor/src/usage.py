"""Pure: the usage block parsed into hourly on-demand-equivalent cost series ($/hr), one per scope.

Hourly input buckets by hour, daily input by day (each day spread as H_day / 24 per hour). Missing buckets
inside the span are filled with 0 and counted as gaps; with more than 5% gaps the span is trimmed to the
longest continuous stretch when that stretch is still 14+ days. `compute` is the account-wide series (all
groups combined, or the rows labelled `compute` when there are any)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import tabular
from constants import COMPUTE, GAP_TRIM_SHARE, HOURS_PER_DAY, MIN_HOURS

TIME_COLUMNS = ("timestamp", "date", "time", "hour", "day", "usage_start_date", "usage_date")
GROUP_COLUMNS = ("scope", "instance_family", "family")
REGION_COLUMNS = ("region", "location")
OD_COLUMNS = ("on_demand_cost", "ondemand_cost", "od_cost")
OTHER_COST_COLUMNS = ("usage_cost", "unblended_cost", "cost")
OPTION_COLUMNS = ("purchase_option", "pricing_model")
EQUIVALENT_COLUMNS = ("ondemand_equivalent_cost", "on_demand_equivalent_cost")
COVERED_OPTIONS = frozenset({"ri", "sp", "reserved", "savings_plan", "savings plan"})
NOT_ON_DEMAND = ("Cost column {col} is treated as on-demand-equivalent; if these costs already reflect "
                 "commitments, say so.")
COVERED_EXCLUDED = ("{n} rows billed under a commitment (purchase_option ri/sp) were excluded: add an "
                    "ondemand_equivalent_cost column to count them.")


class UsageError(ValueError):
    """The usage block has no time or cost column, or no readable row."""


@dataclass(frozen=True)
class Usage:
    granularity: str                        # hourly | daily
    start: datetime                         # first hour of the (possibly trimmed) span, UTC
    series: dict[str, tuple[float, ...]]    # scope -> hourly on-demand-equivalent cost
    spend: dict[str, float]
    rows_read: int
    rows_used: int
    rows_dropped: int
    gap_hours: int
    trimmed: bool
    notes: tuple[str, ...] = ()

    @property
    def hours(self) -> int:
        return len(self.series[COMPUTE])

    @property
    def days(self) -> float:
        return self.hours / HOURS_PER_DAY

    @property
    def end_date(self) -> date:
        return (self.start + timedelta(hours=self.hours - 1)).date()

    @property
    def gap_share(self) -> float:
        return self.gap_hours / max(self.hours, 1)


def _row_cost(row, cols: dict[str, int | None]) -> tuple[float | None, bool]:
    """(cost, excluded_as_covered)."""
    option = tabular.cell(row, cols["option"]).casefold()
    if option in COVERED_OPTIONS:
        equivalent = tabular.number(tabular.cell(row, cols["equivalent"]))
        return (equivalent, False) if equivalent is not None else (None, True)
    return tabular.number(tabular.cell(row, cols["cost"])), False


def _columns(table: tabular.Table) -> tuple[dict[str, int | None], tuple[str, ...]]:
    cost = table.column(*OD_COLUMNS)
    notes: tuple[str, ...] = ()
    if cost is None:
        cost = table.column(*OTHER_COST_COLUMNS)
        if cost is not None:
            notes = (NOT_ON_DEMAND.format(col=table.headers[cost]),)
    cols = {"time": table.column(*TIME_COLUMNS), "cost": cost, "group": table.column(*GROUP_COLUMNS),
            "region": table.column(*REGION_COLUMNS), "option": table.column(*OPTION_COLUMNS),
            "equivalent": table.column(*EQUIVALENT_COLUMNS)}
    if cols["time"] is None or cost is None:
        raise UsageError("no time or cost column")
    return cols, notes


def _group(row, cols) -> str:
    group = tabular.cell(row, cols["group"]) or COMPUTE
    if group.casefold() == COMPUTE:
        return COMPUTE
    region = tabular.cell(row, cols["region"])
    return f"{group}/{region}" if region else group


def parse(text: str) -> Usage:
    table = tabular.read(text)
    cols, notes = _columns(table)
    points: list[tuple[str, datetime, float]] = []
    dropped = covered = 0
    for row in table.rows:
        when = tabular.timestamp(tabular.cell(row, cols["time"]))
        cost, excluded = _row_cost(row, cols)
        covered += excluded
        if when is None or cost is None or cost < 0:
            dropped += not excluded
            continue
        points.append((_group(row, cols), when, cost))
    if not points:
        raise UsageError("no readable rows")
    if covered:
        notes += (COVERED_EXCLUDED.format(n=covered),)
    daily = all(p[1].hour == 0 and p[1].minute == 0 for p in points)
    return _build(points, daily, len(table.rows), dropped + covered, notes)


def _build(points, daily: bool, rows_read: int, dropped: int, notes: tuple[str, ...]) -> Usage:
    step = timedelta(days=1) if daily else timedelta(hours=1)
    per = HOURS_PER_DAY if daily else 1
    buckets: dict[str, dict[datetime, float]] = defaultdict(lambda: defaultdict(float))
    for group, when, cost in points:
        key = datetime(when.year, when.month, when.day) if daily else when.replace(minute=0, second=0,
                                                                                   microsecond=0)
        buckets[group][key] += cost
    present = sorted({k for b in buckets.values() for k in b})
    count = int((present[-1] - present[0]) / step) + 1
    index = [present[0] + i * step for i in range(count)]
    seen = set(present)
    lo, hi, trimmed = 0, count, False
    gaps = count - len(seen)
    if gaps / count > GAP_TRIM_SHARE:
        run_lo, run_hi = _longest_run([k in seen for k in index])
        if (run_hi - run_lo) * per >= MIN_HOURS:
            lo, hi, trimmed = run_lo, run_hi, True
            gaps = 0
    window = index[lo:hi]
    groups = {g: [b.get(k, 0.0) for k in window] for g, b in buckets.items()}
    if COMPUTE not in groups:
        groups[COMPUTE] = [sum(v[i] for v in groups.values()) for i in range(len(window))]
    series = {g: tuple(x / per for x in v for _ in range(per)) for g, v in groups.items()}
    spend = {g: round(sum(v), 2) for g, v in groups.items()}
    return Usage("daily" if daily else "hourly", window[0], series, spend, rows_read, len(points), dropped,
                 gaps * per, trimmed, notes)


def _longest_run(flags: list[bool]) -> tuple[int, int]:
    best, start = (0, 0), None
    for i, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            best = max(best, (start, i), key=lambda r: r[1] - r[0])
            start = None
    return best


def groups_by_spend(usage: Usage) -> list[str]:
    return sorted((g for g in usage.series if g != COMPUTE), key=lambda g: -usage.spend[g])
