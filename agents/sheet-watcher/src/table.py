"""Pure: a tracking table from sheet rows (lists of cells) or pasted text. The first row with two or more text
cells is the header; empty and duplicate header names are renamed; rows are padded to the header width and
fully blank rows are skipped. At most MAX_ROWS data rows are kept (the rest is reported, never silently cut).
(Row reading follows the metrics-pull plan, written here, not imported.)"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_ROWS = 5_000
MAX_COLS = 40
_NUMBERISH = re.compile(r"^[\s$€£¥%(),.+-]*\d[\d\s$€£¥%(),.+-]*$")


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]     # data rows, cells as trimmed text, padded to the header width
    row_numbers: tuple[int, ...]          # 1-based position of each data row in the source
    capped: bool = False                  # more than MAX_ROWS data rows: the rest was not read
    renamed: tuple[str, ...] = ()

    def index(self, column: str | None) -> int | None:
        if column is None:
            return None
        wanted = column.strip().casefold()
        return next((i for i, h in enumerate(self.header) if h.casefold() == wanted), None)

    def column(self, column: str) -> tuple[str, ...]:
        at = self.index(column)
        return () if at is None else tuple(r[at] for r in self.rows)


def is_blank(cell: Any) -> bool:
    return cell is None or not str(cell).strip()


def from_rows(rows: Sequence[Sequence[Any]]) -> Table | None:
    clean = [[_text(c) for c in row][:MAX_COLS] for row in rows if isinstance(row, (list, tuple))]
    for at, row in enumerate(clean):
        texts = [c for c in row if c and not _NUMBERISH.match(c)]
        if len(texts) >= 2 and len(texts) * 2 >= len([c for c in row if c]):
            return _build(row, clean[at + 1:], at + 2)
    return None


def from_text(text: str) -> Table | None:
    """A pasted table: markdown pipes, tabs, or CSV. None when no table is there."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    for splitter in (_pipe_rows, _tab_rows, _csv_rows):
        rows = splitter(lines)
        if len(rows) >= 2:
            found = from_rows(rows)
            if found is not None:
                return found
    return None


def from_envelope(raw: Any) -> Table | None:
    """`{"columns"|"header": [...], "rows": [[...] | {...}]}`, a list of row objects, or a list of lists."""
    if isinstance(raw, Mapping):
        header = raw.get("columns") or raw.get("header")
        body = raw.get("rows")
        if isinstance(header, list) and isinstance(body, list):
            names = [h.get("name") if isinstance(h, Mapping) else h for h in header]
            return from_rows([names, *[[r.get(n) for n in names] if isinstance(r, Mapping) else r for r in body]])
    if isinstance(raw, list) and raw:
        if all(isinstance(r, Mapping) for r in raw):
            names = list(dict.fromkeys(k for r in raw for k in r))
            return from_rows([names, *[[r.get(k) for k in names] for r in raw]])
        if all(isinstance(r, list) for r in raw):
            return from_rows(raw)
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
    if cell is None:
        return ""
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell).strip()


def _header(raw: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names: list[str] = []
    renamed: list[str] = []
    for i, cell in enumerate(raw):
        name = cell.strip() or f"Column {i + 1}"
        base, n = name, 2
        while name.casefold() in (x.casefold() for x in names):
            name, n = f"{base} ({n})", n + 1
        if name != cell.strip():
            renamed.append(name)
        names.append(name)
    return tuple(names), tuple(renamed)


def _build(raw_header: list[str], body: list[list[str]], first_number: int) -> Table | None:
    header, renamed = _header(raw_header)
    width = len(header)
    if width < 2:
        return None
    rows: list[tuple[str, ...]] = []
    numbers: list[int] = []
    capped = False
    for offset, row in enumerate(body):
        padded = tuple((list(row) + [""] * width)[:width])
        if all(is_blank(c) for c in padded):
            continue
        if len(rows) == MAX_ROWS:
            capped = True
            break
        rows.append(padded)
        numbers.append(first_number + offset)
    if not rows:
        return None
    return Table(header, tuple(rows), tuple(numbers), capped, renamed)
