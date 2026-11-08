"""Pure: values far from the rest (Tukey fences, never named as such) and data-quality facts, in the order
the "watch out" sentence picks them: a total row that does not add up, an incomplete last period, mixed
units, negative counts, blank cells, duplicate labels, too few points."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from statistics import quantiles

from cells import looks_partial
from display import amount, plain
from fact import Draft
from table import Column

FENCE = 1.5
MIN_POINTS_FOR_OUTLIERS = 5
FEW_POINTS = 7       # six points or fewer earn the "treat the trend loosely" watch-out (demo script)
TOTAL_LABEL = re.compile(r"^\s*(?:grand\s+)?(?:total|sum|overall|all)s?\s*:?\s*$", re.IGNORECASE)
COUNT_HEADER = re.compile(r"\b(?:count|orders?|units?|users?|visits?|customers?|tickets?|sign-?ups?|sessions?|"
                          r"number|people|deals|calls|downloads)\b|#", re.IGNORECASE)


def outliers(labels: Sequence[str], column: Column) -> list[Draft]:
    pairs = [(lbl, v) for lbl, v in zip(labels, column.values) if v is not None]
    if len(pairs) < MIN_POINTS_FOR_OUTLIERS:
        return []
    q1, _, q3 = quantiles([v for _, v in pairs], n=4, method="inclusive")
    low, high = q1 - FENCE * (q3 - q1), q3 + FENCE * (q3 - q1)
    out = []
    for lbl, v in pairs:
        if low <= v <= high:
            continue
        side = "higher" if v > high else "lower"
        a = amount(v, column.unit)
        out.append(Draft("outlier", f"{lbl}'s {column.name} ({a}) is much {side} than the others.", (a,),
                         {"label": lbl, "value": v, "side": side}))
    return out


def total_rows(labels: Sequence[str]) -> list[int]:
    return [i for i, lbl in enumerate(labels) if TOTAL_LABEL.match(lbl or "")]


def total_check(labels: Sequence[str], column: Column, totals: Sequence[int]) -> Draft | None:
    if not totals:
        return None
    i = totals[0]
    stated = column.values[i]
    rest = sum(v for n, v in enumerate(column.values) if v is not None and n not in totals)
    if stated is None or column.unit.pct or abs(stated - rest) <= max(abs(rest) * 1e-9, 1e-9):
        return None
    a, b = amount(stated, column.unit), amount(rest, column.unit)
    return Draft("quality", f"The {labels[i]} row says {a}, but the other rows add up to {b}.", (a, b),
                 {"stated": stated, "sum": rest})


def quality_facts(labels: Sequence[str], columns: Sequence[Column], time_series: bool, points: int) -> list[Draft]:
    out: list[Draft] = []
    if time_series and labels and looks_partial(labels[-1]):
        out.append(Draft("quality", f"The last period ({labels[-1]}) looks incomplete, so compare it with care.", (),
                         {"partial": labels[-1]}))
    mixed = [c.name for c in columns if c.mixed]
    if mixed:
        out.append(Draft("quality", f"{_names(mixed)} mixes different units, so some values may not compare.", (),
                         {"mixed": mixed}))
    negative = [c.name for c in columns if COUNT_HEADER.search(c.name) and any((v or 0) < 0 for v in c.values)]
    if negative:
        out.append(Draft("quality", f"{_names(negative)} has negative values, which is odd for a count.", (),
                         {"negative": negative}))
    blanks = {c.name: sum(1 for raw in c.raw if not raw.strip()) for c in columns}
    blank_total = sum(blanks.values())
    if blank_total:
        where = _names([n for n, k in blanks.items() if k])
        said = "1 cell in {} is blank; I left it out." if blank_total == 1 else \
            f"{plain(blank_total)} cells in {{}} are blank; I left them out."
        out.append(Draft("quality", said.format(where), (plain(blank_total),), {"blank": blank_total}))
    dupes = [lbl for lbl, k in Counter(labels).items() if k > 1 and lbl.strip()]
    if dupes:
        out.append(Draft("quality", f"{_names(dupes)} appears more than once, so those rows are counted separately.",
                         (), {"duplicates": dupes}))
    if time_series and points < FEW_POINTS:
        out.append(Draft("quality", f"There are only {plain(points)} data points, so treat the trend loosely.",
                         (plain(points),), {"points": points}))
    return out


def _names(names: Sequence[str]) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
