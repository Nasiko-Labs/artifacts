"""Pure: data-quality caveats, always computed in code and listed when present."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from fmt import day, fmt, pct
from profiling import ID_NAME, Table
from result import Result

NULL_CAVEAT_PCT = 20.0
SMALL_TABLE_ROWS = 30
GAP_FACTOR = 3
MIN_DATES_FOR_GAPS = 5
ID_LIKE_MIN_ROWS = 10


def data_caveats(table: Table, results: Sequence[Result] = ()) -> tuple[str, ...]:
    p = table.profile
    out: list[str] = []
    if p.rows < SMALL_TABLE_ROWS:
        out.append(f"Only {fmt(p.rows)} rows: statistics on a table this small are weak.")
    blanks = [f"{c.name} ({pct(c.nulls_pct)})" for c in p.columns if c.nulls_pct >= NULL_CAVEAT_PCT]
    if blanks:
        out.append(f"Mostly blank or patchy columns: {', '.join(blanks[:6])}.")
    if p.mixed_type_cols:
        out.append(f"Mixed numbers and text in: {', '.join(p.mixed_type_cols[:6])}; I treated them as text.")
    if p.duplicate_rows:
        out.append(f"{fmt(p.duplicate_rows)} rows are exact duplicates; they are counted as they are.")
    if p.constant_cols:
        out.append(f"Columns with a single value throughout: {', '.join(p.constant_cols[:6])}.")
    out += _id_like(table)
    out += _date_gaps(table)
    if p.month_first_assumed:
        out.append(f"Dates in {', '.join(p.month_first_assumed)} could be day-first or month-first; I read them "
                   "month-first.")
    for result in results:
        out += [flag for flag in result.flags if flag not in out]
    return tuple(out)


def _id_like(table: Table) -> list[str]:
    out = []
    for c in table.profile.columns:
        if c.dtype != "numeric":
            continue
        s = table.frame[c.name].dropna()
        whole = len(s) >= ID_LIKE_MIN_ROWS and bool((s == s.round()).all()) and s.nunique() == len(s)
        if ID_NAME.search(c.name) or (whole and table.profile.rows == len(s)):
            out.append(f"{c.name} looks like an identifier, not a quantity; its sums and averages mean little.")
    return out


def _date_gaps(table: Table) -> list[str]:
    out = []
    for name in table.profile.of("datetime"):
        dates = pd.Series(table.frame[name].dropna().dt.normalize().unique()).sort_values()
        if len(dates) < MIN_DATES_FOR_GAPS:
            continue
        steps = dates.diff().dropna()
        median = steps.median()
        if median <= pd.Timedelta(0):
            continue
        big = steps[steps > GAP_FACTOR * median]
        if not big.empty:
            at = big.idxmax()
            out.append(f"{name} has {len(big)} gaps longer than {GAP_FACTOR}× the usual spacing (largest: "
                       f"{big.max().days} days before {day(dates[at])}).")
    return out
