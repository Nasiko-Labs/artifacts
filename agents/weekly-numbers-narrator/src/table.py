"""Pure: a metrics table from sheet rows (lists of cells) or pasted text, with each column typed in code:
the period column, notes/commentary columns, metric columns (mostly numbers) and an optional `threshold`
row giving a per-metric minimum absolute change. Numbers keep the column's own display rules: decimals
(the most seen in the column), a percent sign, a currency prefix and thousands grouping.
(Header typing and number parsing follow the metrics-pull plan, written here, not imported.)"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MAX_ROWS = 500
MAX_COLS = 40
_PERIOD_HEADER = re.compile(r"\b(week|wk|date|period|month|day|as of)\b", re.IGNORECASE)
_NOTES_HEADER = re.compile(r"\b(notes?|comments?|commentary|context|why|remarks?|annotations?)\b", re.IGNORECASE)
_THRESHOLD_LABEL = re.compile(r"^\s*(threshold|min[ _]?abs|minimum change)\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"^(?P<neg>-|\()?\s*(?P<prefix>[$€£¥])?\s*(?P<num>-?\d[\d,]*(?:\.\d+)?|-?\.\d+)\s*"
                     r"(?P<pct>%)?\s*\)?$")
_BLANK = frozenset({"", "-", "—", "–", "n/a", "na", "null", "none"})
_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
_MIN_METRIC_SHARE = 0.5


@dataclass(frozen=True)
class Number:
    value: float
    decimals: int
    percent: bool = False
    prefix: str = ""
    grouped: bool = False


def parse_number(raw: Any) -> Number | None:
    """A cell as a number, or None (blank or text). Booleans are not numbers."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        text = repr(raw) if isinstance(raw, float) else str(raw)
        return Number(float(raw), _decimals(text))
    text = str(raw).strip()
    if text.casefold() in _BLANK:
        return None
    match = _NUMBER.match(text)
    if not match:
        return None
    num = match.group("num")
    if num.count(",") and not _GROUPED.match(num.lstrip("-")):
        return None
    value = float(num.replace(",", ""))
    negative = bool(match.group("neg")) and value > 0
    return Number(-value if negative else value, _decimals(num), bool(match.group("pct")),
                  match.group("prefix") or "", "," in num)


def _decimals(text: str) -> int:
    if "e" in text.casefold():
        return 2
    return len(text.split(".", 1)[1]) if "." in text else 0


def is_blank(raw: Any) -> bool:
    return raw is None or str(raw).strip().casefold() in _BLANK


@dataclass(frozen=True)
class Column:
    index: int
    name: str
    kind: str                # "period" | "metric" | "notes" | "other"
    decimals: int = 0
    percent: bool = False
    prefix: str = ""
    grouped: bool = False


@dataclass(frozen=True)
class Table:
    columns: tuple[Column, ...]
    rows: tuple[tuple[str, ...], ...]     # data rows, cells as text, padded to the header width
    row_numbers: tuple[int, ...]          # 1-based position of each data row in the source (header = its own)
    source: str                            # "sheet:<title>" | "pasted" | "envelope"
    thresholds: tuple[tuple[str, float], ...] = ()

    @property
    def period(self) -> Column:
        return next(c for c in self.columns if c.kind == "period")

    @property
    def metrics(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if c.kind == "metric")

    @property
    def notes(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if c.kind == "notes")

    def threshold(self, metric: str) -> float | None:
        return dict(self.thresholds).get(metric)

    def to_dict(self) -> dict:
        header = [c.name for c in self.columns]
        return {"header": header, "rows": [list(r) for r in self.rows], "row_numbers": list(self.row_numbers),
                "source": self.source, "thresholds": [list(t) for t in self.thresholds]}

    @staticmethod
    def from_dict(data: dict) -> Table | None:
        header = [str(h) for h in data.get("header") or ()]
        rows = [[str(c) for c in r] for r in data.get("rows") or ()]
        thresholds = tuple((str(k), float(v)) for k, v in data.get("thresholds") or ())
        built = _build(header, rows, [int(n) for n in data.get("row_numbers") or ()], str(data.get("source") or ""))
        return None if built is None else Table(built.columns, built.rows, built.row_numbers, built.source,
                                                thresholds or built.thresholds)


def from_rows(rows: Sequence[Sequence[Any]], source: str) -> Table | None:
    """The table in sheet rows: the first row with two or more text cells is the header. None when no
    period column and at least one metric column can be found."""
    clean = [[_text(c) for c in row][:MAX_COLS] for row in rows[: MAX_ROWS + 1] if isinstance(row, (list, tuple))]
    for at, row in enumerate(clean):
        texts = [c for c in row if c and parse_number(c) is None]
        if len(texts) >= 2 and len(texts) * 2 >= len([c for c in row if c]):
            body = clean[at + 1:]
            numbers = [at + 2 + i for i in range(len(body))]
            return _build(row, body, numbers, source)
    return None


def from_text(text: str, source: str = "pasted") -> Table | None:
    """A pasted table: markdown pipes, tabs, or CSV. None when no table is there."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    for splitter in (_pipe_rows, _tab_rows, _csv_rows):
        rows = splitter(lines)
        if len(rows) >= 3:
            table = from_rows(rows, source)
            if table is not None:
                return table
    return None


def _pipe_rows(lines: list[str]) -> list[list[str]]:
    rows = [ln.strip().strip("|").split("|") for ln in lines if ln.count("|") >= 2]
    return [[c.strip() for c in r] for r in rows if not all(re.fullmatch(r"\s*:?-{2,}:?\s*", c) for c in r)]


def _tab_rows(lines: list[str]) -> list[list[str]]:
    return [[c.strip() for c in ln.split("\t")] for ln in lines if "\t" in ln]


def _csv_rows(lines: list[str]) -> list[list[str]]:
    candidates = [ln for ln in lines if ln.count(",") >= 1]
    rows = list(csv.reader(io.StringIO("\n".join(candidates))))
    if not rows:
        return []
    width = len(rows[0])
    return [[c.strip() for c in r] for r in rows if len(r) == width] if width >= 2 else []


def _text(cell: Any) -> str:
    if cell is None or isinstance(cell, bool):
        return "" if cell is None else str(cell)
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell).strip()


def _build(header: list[str], body: list[list[str]], numbers: list[int], source: str) -> Table | None:
    width = len(header)
    if width < 2:
        return None
    rows, row_numbers, thresholds_row = [], [], None
    for row, number in zip(body, numbers or range(2, len(body) + 2)):
        padded = tuple((list(row) + [""] * width)[:width])
        if all(is_blank(c) for c in padded):
            continue
        if any(_THRESHOLD_LABEL.match(c) for c in padded[:1]):
            thresholds_row = padded
            continue
        rows.append(padded)
        row_numbers.append(number)
    if not rows:
        return None
    columns = _type_columns(header, rows)
    if columns is None:
        return None
    thresholds = ()
    if thresholds_row is not None:
        parsed = [(c.name, parse_number(thresholds_row[c.index])) for c in columns if c.kind == "metric"]
        thresholds = tuple((name, abs(n.value)) for name, n in parsed if n is not None)
    return Table(columns, tuple(rows), tuple(row_numbers), source, thresholds)


def _type_columns(header: list[str], rows: list[tuple[str, ...]]) -> tuple[Column, ...] | None:
    names = [h.strip() or f"Column {i + 1}" for i, h in enumerate(header)]
    period = next((i for i, n in enumerate(names) if _PERIOD_HEADER.search(n)), None)
    if period is None:
        period = next((i for i in range(len(names)) if not _mostly_numbers(rows, i)), None)
    if period is None:
        return None
    columns = []
    for i, name in enumerate(names):
        if i == period:
            columns.append(Column(i, name, "period"))
        elif _NOTES_HEADER.search(name):
            columns.append(Column(i, name, "notes"))
        elif _mostly_numbers(rows, i):
            parsed = [n for n in (parse_number(r[i]) for r in rows) if n is not None]
            columns.append(Column(i, name, "metric", max(n.decimals for n in parsed), any(n.percent for n in parsed),
                                  next((n.prefix for n in parsed if n.prefix), ""), any(n.grouped for n in parsed)))
        else:
            columns.append(Column(i, name, "other"))
    if not any(c.kind == "metric" for c in columns):
        return None
    return tuple(columns)


def _mostly_numbers(rows: list[tuple[str, ...]], index: int) -> bool:
    filled = [r[index] for r in rows if not is_blank(r[index])]
    if not filled:
        return False
    numeric = sum(1 for c in filled if parse_number(c) is not None)
    return numeric > 0 and numeric >= _MIN_METRIC_SHARE * len(filled)
