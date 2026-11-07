"""Pure: what each column of the pasted table is: a segment (dimension) column, a number column, and for a
number column which period (before/after) and which role (numerator/denominator) its name says."""

from __future__ import annotations

import re
from datetime import date

from grid import Grid, parse_number

NUMERIC_SHARE = 0.8
BEFORE = ("before", "prior", "previous", "prev", "baseline", "last_week", "last_month", "last_period", "period_1",
          "p1", "from", "pre", "base", "week_1", "month_1")
AFTER = ("after", "current", "now", "this_week", "this_month", "this_period", "period_2", "p2", "to", "post",
         "curr", "week_2", "month_2")
NUM = ("num", "numerator", "conversions", "conversion", "converted", "events", "signups", "signup", "orders",
       "purchases", "successes", "conv", "x", "wins", "sign_ups")
DEN = ("den", "denominator", "denom", "sessions", "users", "visits", "visitors", "total", "n", "trials",
       "impressions", "traffic", "leads", "views")
PERIOD_NAMES = ("period", "week", "month", "window", "date", "phase", "when", "time", "day")
TOTAL_LABELS = frozenset({"total", "all", "grand total", "overall", "sum", "totals"})
_NON_WORD = re.compile(r"[^a-z0-9]+")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def norm(name: str) -> str:
    return _NON_WORD.sub("_", (name or "").casefold()).strip("_")


def _has(name: str, words: tuple[str, ...]) -> bool:
    wrapped = f"_{norm(name)}_"
    return any(f"_{w}_" in wrapped for w in words)


def period_of(name: str) -> str | None:
    before, after = _has(name, BEFORE), _has(name, AFTER)
    if before == after:
        return None
    return "before" if before else "after"


def role_of(name: str) -> str | None:
    num, den = _has(name, NUM), _has(name, DEN)
    if num == den:
        return None
    return "num" if num else "den"


def numeric_columns(grid: Grid) -> list[int]:
    """Columns whose non-blank cells are (at least 80%) numbers."""
    out = []
    for i in range(len(grid.header)):
        cells = [row[i] for row in grid.rows if row[i].strip()]
        if cells and sum(parse_number(c) is not None for c in cells) >= NUMERIC_SHARE * len(cells):
            out.append(i)
    return out


def iso_date(text: str) -> date | None:
    match = _ISO.match((text or "").strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def order_periods(labels: list[str]) -> tuple[tuple[str, str], str]:
    """(before label, after label) and how the order was decided: by the labels' own words, by date, or
    alphabetically."""
    a, b = labels
    pa, pb = period_of(a), period_of(b)
    if pa and pb and pa != pb:
        return ((a, b) if pa == "before" else (b, a)), "by their names"
    da, db = iso_date(a), iso_date(b)
    if da and db and da != db:
        return ((a, b) if da < db else (b, a)), "earlier date first"
    return tuple(sorted((a, b), key=str.casefold)), "alphabetical order"


def period_column(grid: Grid, numeric: list[int]) -> tuple[int, list[str]] | None:
    """A long layout's period column: named like a period (or holding before/after labels), its distinct
    values in first-seen order."""
    for i, name in enumerate(grid.header):
        if i in numeric and not iso_date(grid.rows[0][i]):
            continue
        values = list(dict.fromkeys(row[i] for row in grid.rows if row[i].strip()))
        labelled = len(values) == 2 and {period_of(v) for v in values} == {"before", "after"}
        if _has(name, PERIOD_NAMES) or norm(name) in PERIOD_NAMES or labelled:
            return i, values
    return None
