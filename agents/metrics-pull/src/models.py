"""The output contract, built in code:

    Table{columns: [{name, type}], rows, src_rows, row_count, blank_count, parse_errors, as_of,
          source{title, tab, range, modified|null}}
    Envelope{summary, items[=rows as objects + src_rows], confidence, needs_human, table, spec, coverage[], notes[]}

`confidence` and `needs_human` are computed from checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import coltypes
from engine import Result

MAX_DATA_ROWS = 2000
LOW_PARSE_ERROR_SHARE = 0.05


@dataclass(frozen=True)
class Quality:
    """What code found while reading and running the query."""

    pasted: bool = False
    capped: bool = False
    partial: bool = False
    picked_column: bool = False
    ambiguous_dates: tuple[str, ...] = ()
    mixed_units: tuple[str, ...] = ()
    parse_errors: int = 0
    parse_error_samples: tuple[str, ...] = ()
    numeric_cells: int = 0
    dropped: int = 0                     # spec parts dropped by the verbatim check
    missing: tuple[str, ...] = ()        # requested columns that do not exist
    coverage: tuple[str, ...] = ()

    @property
    def confidence(self) -> str:
        share = self.parse_errors / self.numeric_cells if self.numeric_cells else 0.0
        if self.pasted or share > LOW_PARSE_ERROR_SHARE:
            return "low"
        if (self.capped or self.partial or self.picked_column or self.ambiguous_dates or self.mixed_units
                or self.dropped or self.missing or self.parse_errors):
            return "medium"
        return "high"

    @property
    def needs_human(self) -> bool:
        return bool(self.missing or self.parse_errors)


@dataclass(frozen=True)
class Source:
    title: str
    tab: str = ""
    range: str = ""
    modified: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "tab": self.tab, "range": self.range, "modified": self.modified or None}


@dataclass(frozen=True)
class Pull:
    """One answered request: what render.py turns into markdown and the envelope."""

    result: Result
    source: Source
    as_of: datetime
    spec: Mapping[str, Any]
    quality: Quality
    columns_available: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())
    label: str = ""


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(round(value, 10))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def table(pull: Pull) -> dict:
    result = pull.result
    rows = result.rows[:MAX_DATA_ROWS]
    return {
        "columns": [c.to_dict() for c in result.columns],
        "rows": [[json_value(v) for v in r] for r in rows],
        "src_rows": [list(s) for s in result.src_rows[:MAX_DATA_ROWS]],
        "row_count": result.total_rows,
        "blank_count": result.blank_count,
        "parse_errors": pull.quality.parse_errors,
        "as_of": pull.as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": pull.source.to_dict(),
    }


def items(pull: Pull) -> list[dict]:
    names = [c.name for c in pull.result.columns]
    out = []
    for row, src in zip(pull.result.rows[:MAX_DATA_ROWS], pull.result.src_rows[:MAX_DATA_ROWS], strict=True):
        out.append({**{n: json_value(v) for n, v in zip(names, row, strict=True)}, "src_rows": list(src)})
    return out


def envelope(pull: Pull, summary: str) -> dict:
    return {
        "summary": summary,
        "items": items(pull),
        "confidence": pull.quality.confidence,
        "needs_human": pull.quality.needs_human,
        "table": table(pull),
        "spec": dict(pull.spec),
        "coverage": list(pull.quality.coverage),
        "missing_columns": list(pull.quality.missing),
        "notes": list(pull.notes),
    }


def numeric_checks(columns: Sequence[Any], rows: Sequence[Any]) -> tuple[int, tuple[str, ...], int]:
    """(parse errors, up to five sample texts, numeric cells) over the number columns."""
    errors, samples, cells = 0, [], 0
    for i, column in enumerate(columns):
        if column.type != "number":
            continue
        for row in rows:
            cell = row.cells[i]
            if coltypes.is_blank(cell):
                continue
            cells += 1
            if coltypes.parse_number(cell) is None:
                errors += 1
                if len(samples) < 5:
                    samples.append(f"{column.name} row {row.number}: {str(cell)[:40]}")
    return errors, tuple(samples), cells


def mixed_units(columns: Sequence[Any], rows: Sequence[Any]) -> tuple[str, ...]:
    """Number columns whose currency symbol varies, and unit/currency columns with several values."""
    flagged = []
    for i, column in enumerate(columns):
        cells = [r.cells[i] for r in rows if not coltypes.is_blank(r.cells[i])]
        if column.type == "number":
            symbols = {coltypes.currency_of(c) for c in cells} - {None}
            if len(symbols) > 1:
                flagged.append(column.name)
        elif column.name.casefold() in ("currency", "unit", "units") and len({str(c).strip() for c in cells}) > 1:
            flagged.append(column.name)
    return tuple(flagged)
