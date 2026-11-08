"""Pure: a chart slide's data, read from the parsed table (never from the model), its type chosen by a
code rule, and the changes computed in code that the model may quote (GIVEN)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from checks import MAX_CHART_CATEGORIES, MAX_CHART_SERIES
from dataparse import Table, to_number
from models import ChartRef

MAX_PIE_PARTS = 5
_TIME_LIKE = re.compile(r"^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s*'?\d{2,4})?$|"
                        r"^q[1-4](?:\s*'?\d{2,4})?$|^(?:fy)?\s*'?\d{2,4}$|^\d{4}-\d{2}(?:-\d{2})?$|"
                        r"^(?:h[12]|w(?:ee)?k\s*\d{1,2})(?:\s*\d{2,4})?$", re.IGNORECASE)


@dataclass(frozen=True)
class ChartData:
    kind: str                                  # column | bar | line | pie
    categories: tuple[str, ...]
    series: tuple[tuple[str, tuple[float, ...]], ...]
    note: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "categories": list(self.categories),
                "series": [[name, list(values)] for name, values in self.series]}


def is_time_like(labels: tuple[str, ...]) -> bool:
    return bool(labels) and all(_TIME_LIKE.match(label.strip()) for label in labels)


def chart_data(ref: ChartRef, table: Table) -> ChartData:
    label_idx = table.column(ref.label_column)
    numeric = {c.casefold() for c in table.numeric_columns()}
    names = [c for c in ref.value_columns if c.casefold() in numeric][:MAX_CHART_SERIES]
    indexes = [table.column(n) for n in names]
    categories = tuple(row[label_idx] for row in table.rows)
    series = tuple((table.columns[i], tuple(to_number(row[i]) or 0.0 for row in table.rows)) for i in indexes)
    categories, series, note = _limit(categories, series)
    return ChartData(_kind(ref, categories, series), categories, series, note)


def _limit(categories, series):
    if len(categories) <= MAX_CHART_CATEGORIES:
        return categories, series, ""
    if is_time_like(categories):  # a time series keeps its most recent points
        keep = MAX_CHART_CATEGORIES
        return (categories[-keep:], tuple((n, v[-keep:]) for n, v in series),
                f"The chart shows the last {keep} of {len(categories)} points.")
    head = MAX_CHART_CATEGORIES - 1
    grouped = tuple((n, (*v[:head], sum(v[head:]))) for n, v in series)
    return ((*categories[:head], "Other"), grouped,
            f"{len(categories) - head} smallest-listed categories were grouped as \"Other\".")


def _kind(ref: ChartRef, categories, series) -> str:
    if is_time_like(categories):
        return "line"
    wants_pie = "pie" in ref.kind.casefold()
    if (wants_pie and len(series) == 1 and len(categories) <= MAX_PIE_PARTS
            and all(v > 0 for v in series[0][1])):
        return "pie"
    longest = max((len(c) for c in categories), default=0)
    return "bar" if longest > 20 else "column"


def axis_max(data: ChartData) -> float:
    top = max((v for _, values in data.series for v in values), default=0.0)
    return top * 1.15 if top > 0 else 1.0


def given_changes(tables: dict[str, Table]) -> list[str]:
    """For every numeric column, first → last with the % change, computed here (the model may quote these)."""
    given: list[str] = []
    for table in tables.values():
        if len(table.rows) < 2:
            continue
        labels = (table.rows[0][0], table.rows[-1][0])
        for name in table.numeric_columns():
            idx = table.column(name)
            first, last = to_number(table.rows[0][idx]), to_number(table.rows[-1][idx])
            if first is None or last is None:
                continue
            total = sum(to_number(r[idx]) or 0.0 for r in table.rows)
            change = f", {(last - first) / abs(first) * 100:+.1f}%" if first else ""
            given.append(f"{table.name} {name}: {labels[0]} {table.rows[0][idx]} -> {labels[1]} "
                         f"{table.rows[-1][idx]}{change}; total {total:g}")
    return given
