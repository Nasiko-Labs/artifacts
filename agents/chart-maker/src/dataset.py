"""Pure: a parsed table -> the dataset a chart is drawn from (x labels, numeric series), with every
unparseable cell counted, rows with no number excluded and named, and repeated labels added together."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from dataparse import Table, parse_number

MAX_FAILED_SHOWN = 5
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_TIME_LABEL = re.compile(
    r"^(?:(?:19|20)\d{2}(?:[-/ ]?(?:q[1-4]|h[12]|\d{1,2}(?:[-/]\d{1,2})?))?"
    r"|q[1-4](?:[ -]?(?:19|20)?\d{2})?|h[12](?:[ -]?(?:19|20)?\d{2})?|w(?:eek)?\s?\d{1,2}"
    r"|(?:" + "|".join(_MONTHS) + r")[a-z]*\.?(?:[ -]?(?:19|20)?\d{2})?|\d{1,2}/\d{1,2}(?:/\d{2,4})?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Dataset:
    x: str
    series: tuple[str, ...]
    labels: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]     # values[s][i]; a failed cell counts as 0 and is listed
    failed: tuple[str, ...] = ()              # "row 3, Revenue: 'n/a'"
    failed_cells: int = 0
    total_cells: int = 0
    excluded_rows: int = 0
    merged_rows: int = 0
    ordinal: bool = False                     # x is time-like or ordered: never sort by value
    other_count: int = 0                      # categories folded into "Other"
    total_categories: int = 0

    def to_dict(self) -> dict:
        return {"x": self.x, "series": list(self.series), "labels": list(self.labels),
                "values": [list(v) for v in self.values], "failed": list(self.failed),
                "failed_cells": self.failed_cells, "total_cells": self.total_cells,
                "excluded_rows": self.excluded_rows, "merged_rows": self.merged_rows, "ordinal": self.ordinal,
                "other_count": self.other_count, "total_categories": self.total_categories}

    @staticmethod
    def from_dict(data: dict | None) -> Dataset | None:
        if not data:
            return None
        return Dataset(x=data["x"], series=tuple(data["series"]), labels=tuple(data["labels"]),
                       values=tuple(tuple(float(v) for v in s) for s in data["values"]),
                       failed=tuple(data.get("failed") or ()), failed_cells=int(data.get("failed_cells", 0)),
                       total_cells=int(data.get("total_cells", 0)), excluded_rows=int(data.get("excluded_rows", 0)),
                       merged_rows=int(data.get("merged_rows", 0)), ordinal=bool(data.get("ordinal")),
                       other_count=int(data.get("other_count", 0)),
                       total_categories=int(data.get("total_categories", 0)))

    @property
    def has_negative(self) -> bool:
        return any(v < 0 for s in self.values for v in s)

    def column(self, name: str) -> tuple[float, ...]:
        return self.values[self.series.index(name)]


def numeric_columns(table: Table) -> tuple[str, ...]:
    """Columns where at least half of the non-empty cells are numbers (the x column excluded)."""
    x = x_column(table)
    return tuple(c for i, c in enumerate(table.columns) if c != x and _mostly_numeric(table, i))


def x_column(table: Table) -> str:
    for i, name in enumerate(table.columns):
        if not _mostly_numeric(table, i) or _time_like([r[i] for r in table.rows]):
            return name
    return table.columns[0]


def _mostly_numeric(table: Table, index: int) -> bool:
    cells = [r[index] for r in table.rows if r[index].strip()]
    numbers = sum(parse_number(c) is not None for c in cells)
    return numbers > 0 and numbers * 2 >= len(cells)


def _time_like(labels: list[str]) -> bool:
    cleaned = [lb.strip() for lb in labels if lb.strip()]
    return bool(cleaned) and all(_TIME_LABEL.match(lb) for lb in cleaned)


def build_dataset(table: Table, x: str, series: tuple[str, ...]) -> Dataset:
    xi = table.columns.index(x)
    indexes = [table.columns.index(s) for s in series]
    order: list[str] = []
    sums: dict[str, list[float]] = {}
    failed: list[str] = []
    failed_cells = excluded = 0
    for row_no, row in enumerate(table.rows, start=1 + int(table.had_header)):
        numbers = [parse_number(row[i]) for i in indexes]
        for name, cell, number in zip(series, (row[i] for i in indexes), numbers, strict=True):
            if number is None:
                failed_cells += 1
                failed.append(f"row {row_no}, {name}: '{cell[:30]}'")
        if all(n is None for n in numbers):
            excluded += 1
            continue
        label = row[xi].strip() or f"(row {row_no})"
        if label not in sums:
            order.append(label)
            sums[label] = [0.0] * len(series)
        sums[label] = [a + (n or 0.0) for a, n in zip(sums[label], numbers, strict=True)]
    merged = len(table.rows) - excluded - len(order)
    values = tuple(tuple(sums[lb][s] for lb in order) for s in range(len(series)))
    return Dataset(x=x, series=series, labels=tuple(order), values=values, failed=tuple(failed),
                   failed_cells=failed_cells, total_cells=len(table.rows) * len(series), excluded_rows=excluded,
                   merged_rows=merged, ordinal=_time_like(list(order)), total_categories=len(order))


def rename_label(ds: Dataset, old: str, new: str) -> Dataset | None:
    """A label or series renamed by the user (their words); None when `old` names neither."""
    fold = old.casefold()
    labels = tuple(new if lb.casefold() == fold else lb for lb in ds.labels)
    series = tuple(new if s.casefold() == fold else s for s in ds.series)
    x = new if ds.x.casefold() == fold else ds.x
    if labels == ds.labels and series == ds.series and x == ds.x:
        return None
    return replace(ds, labels=labels, series=series, x=x)


def transpose(ds: Dataset) -> Dataset:
    """Series become categories and categories become series (swap axes)."""
    values = tuple(tuple(ds.values[s][i] for s in range(len(ds.series))) for i in range(len(ds.labels)))
    return replace(ds, x="Series", series=ds.labels, labels=ds.series, values=values, ordinal=False,
                   total_categories=len(ds.series))
