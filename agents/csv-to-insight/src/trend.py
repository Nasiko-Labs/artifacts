"""Pure: the trend analysis. Buckets are chosen from the date span: up to 60 days daily, up to 26 weeks
weekly (weeks start Monday), up to 2 years monthly, else quarterly. Facts: first → last, peak and trough,
the largest step change; a partial last bucket is flagged."""

from __future__ import annotations

import pandas as pd

from fmt import day, fmt, pct
from profiling import Table
from result import Fact, Result, rows

DAILY_MAX_DAYS = 60
WEEKLY_MAX_DAYS = 182
MONTHLY_MAX_DAYS = 731
TABLE_BUCKETS = 12
_RULES = {"day": "D", "week": "W-MON", "month": "MS", "quarter": "QS"}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "quarter": 91}


def bucket_unit(span_days: int) -> str:
    if span_days <= DAILY_MAX_DAYS:
        return "day"
    if span_days <= WEEKLY_MAX_DAYS:
        return "week"
    return "month" if span_days <= MONTHLY_MAX_DAYS else "quarter"


def label(stamp: pd.Timestamp, unit: str) -> str:
    if unit == "week":
        return f"week of {day(stamp)}"
    if unit == "month":
        return stamp.strftime("%Y-%m")
    if unit == "quarter":
        return f"{stamp.year}-Q{(stamp.month - 1) // 3 + 1}"
    return day(stamp)


def bucketed(frame: pd.DataFrame, date: str, value: str, agg: str) -> tuple[pd.Series, str]:
    data = frame[[date, value]].dropna(subset=[date])
    if agg != "count":
        data = data.dropna(subset=[value])
    span = int((data[date].max() - data[date].min()).days) if len(data) else 0
    unit = bucket_unit(span)
    rule = _RULES[unit]
    grouped = data.set_index(date)[value]
    if unit == "week":
        resampled = grouped.resample(rule, label="left", closed="left")
    else:
        resampled = grouped.resample(rule)
    if agg == "count":
        series = resampled.count().astype(float)
    elif agg == "mean":
        series = resampled.mean()
    else:
        series = resampled.sum(min_count=1)
    return series.astype(float), unit


def trend(table: Table, cols: dict, params: dict) -> Result:
    date, value, agg = cols["date"], cols["value"], params.get("agg", "sum")
    series, unit = bucketed(table.frame, date, value, agg)
    what = "rows" if agg == "count" else f"{agg} of {value}"
    present = series.dropna()
    if len(present) < 2:
        return Result("trend", cols, f"{what} per {unit}",
                      facts=(Fact(f"Not enough dated rows to show a trend of {what}."),))
    labels = [label(ts, unit) for ts in series.index]
    first_ts, last_ts = present.index[0], present.index[-1]
    first, last = present.iloc[0], present.iloc[-1]
    change = f", a change of {pct(100 * (last - first) / abs(first))}" if first else ""
    facts = [Fact(f"{what.capitalize()} per {unit} went from {fmt(first)} ({label(first_ts, unit)}) to {fmt(last)} "
                  f"({label(last_ts, unit)}){change}.",
                  (fmt(first), fmt(last), *((pct(100 * (last - first) / abs(first)),) if first else ())))]
    peak_ts, trough_ts = present.idxmax(), present.idxmin()
    facts.append(Fact(f"The peak {unit} is {label(peak_ts, unit)} ({fmt(present.max())}); the lowest is "
                      f"{label(trough_ts, unit)} ({fmt(present.min())}).", (fmt(present.max()), fmt(present.min()))))
    steps = present.diff().dropna()
    if not steps.empty:
        at = steps.abs().idxmax()
        before = present.index[present.index.get_loc(at) - 1]
        facts.append(Fact(f"The largest change between consecutive {unit}s is {fmt(steps[at])} "
                          f"({label(before, unit)} to {label(at, unit)}).", (fmt(steps[at]),)))
    flags = _partial_last(table.frame[date].dropna(), last_ts, unit)
    table_rows = [[lab, fmt(x) if pd.notna(x) else "-"] for lab, x in zip(labels, series)][-TABLE_BUCKETS:]
    chart = {"kind": "line", "title": f"{what} per {unit}", "x": labels,
             "series": {what: [float(x) if pd.notna(x) else None for x in series]}}
    return Result("trend", cols, f"{what} per {unit}", (unit, what), rows(table_rows), tuple(facts), chart, flags,
                  extra={"unit": unit})


def _partial_last(dates: pd.Series, last_bucket: pd.Timestamp, unit: str) -> tuple[str, ...]:
    if unit == "day":
        return ()
    covered = int((dates.max().normalize() - last_bucket.normalize()).days) + 1
    expected = _UNIT_DAYS[unit]
    if unit == "month":
        expected = last_bucket.days_in_month
    if covered >= expected:
        return ()
    return ((f"The last {unit} ({label(last_bucket, unit)}) has only {covered} of {expected} days of data, so it "
             "looks lower than it is."),)
