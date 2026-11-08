"""Pure: pasted tables (CSV/TSV, markdown tables, `label: value` lines) parsed into named tables.

This agent's own copy of the table-parser design (no shared code). Chart and table slides read their
numbers from here, never from the model."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_ROWS = 200
_MD_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
_LABEL_VALUE = re.compile(r"^\s*[-*•]?\s*([^:|,\t]{1,60}):\s*([-+]?[$€£]?\s?[\d.,]+\s*[%kKmMbB]?)\s*$")
_NUMBER = re.compile(r"^([-+]?)[$€£]?\s?(\d[\d,]*(?:\.\d+)?|\.\d+)\s*(%|[kKmMbB])?$")
_SCALE = {"k": 1e3, "m": 1e6, "b": 1e9}


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    truncated: int = 0          # rows dropped over MAX_ROWS

    def column(self, name: str) -> int | None:
        wanted = name.strip().casefold()
        return next((i for i, c in enumerate(self.columns) if c.strip().casefold() == wanted), None)

    def numeric_columns(self) -> tuple[str, ...]:
        return tuple(c for i, c in enumerate(self.columns)
                     if self.rows and all(to_number(r[i]) is not None for r in self.rows))

    def to_dict(self) -> dict:
        return {"name": self.name, "columns": list(self.columns), "rows": [list(r) for r in self.rows],
                "truncated": self.truncated}

    @staticmethod
    def from_dict(data: dict) -> Table:
        return Table(str(data["name"]), tuple(str(c) for c in data["columns"]),
                     tuple(tuple(str(v) for v in r) for r in data["rows"]), int(data.get("truncated") or 0))


def to_number(cell: str) -> float | None:
    """`$4.2M` → 4200000.0, `12%` → 12.0, `1,200` → 1200.0; anything else → None."""
    match = _NUMBER.match(cell.strip())
    if not match:
        return None
    sign, digits, suffix = match.groups()
    value = float(digits.replace(",", ""))
    value *= _SCALE.get((suffix or "").lower(), 1.0)
    return -value if sign == "-" else value


def extract(text: str, first_index: int = 1) -> tuple[str, tuple[Table, ...]]:
    """(the text with CSV/markdown tables removed, the tables found). `label: value` blocks stay in the
    text too: they are notes as well as data."""
    lines = text.splitlines()
    kept: list[str] = []
    tables: list[Table] = []
    i = 0
    while i < len(lines):
        block, end = _table_at(lines, i)
        if block is not None:
            tables.append(_named(block, first_index + len(tables)))
            i = end
            continue
        pairs, end = _pairs_at(lines, i)
        if pairs is not None:
            tables.append(_named(pairs, first_index + len(tables)))
            kept.extend(lines[i:end])
            i = end
            continue
        kept.append(lines[i])
        i += 1
    return "\n".join(kept), tuple(tables)


def _named(table: tuple[tuple[str, ...], tuple[tuple[str, ...], ...]], index: int) -> Table:
    columns, rows = table
    return Table(f"T{index}", columns, rows[:MAX_ROWS], max(0, len(rows) - MAX_ROWS))


def _table_at(lines: list[str], start: int):
    line = lines[start].strip()
    if line.startswith("|"):
        return _markdown(lines, start)
    for sep in ("\t", ","):
        if sep in line:
            found = _delimited(lines, start, sep)
            if found[0] is not None:
                return found
    return None, start


def _markdown(lines: list[str], start: int):
    rows: list[tuple[str, ...]] = []
    i = start
    while i < len(lines) and lines[i].strip().startswith("|"):
        if not _MD_SEPARATOR.match(lines[i].strip()):
            rows.append(tuple(c.strip() for c in lines[i].strip().strip("|").split("|")))
        i += 1
    if len(rows) < 2 or len({len(r) for r in rows}) != 1 or len(rows[0]) < 2:
        return None, start
    return (rows[0], tuple(rows[1:])), i


def _delimited(lines: list[str], start: int, sep: str):
    width = len(lines[start].split(sep))
    i = start
    rows: list[tuple[str, ...]] = []
    while i < len(lines) and lines[i].strip() and len(lines[i].split(sep)) == width:
        rows.append(tuple(c.strip() for c in lines[i].strip().split(sep)))
        i += 1
    if width < 2 or len(rows) < 2:
        return None, start
    body = rows[1:]
    numeric = any(all(to_number(r[c]) is not None for r in body) for c in range(width))
    return ((rows[0], tuple(body)), i) if numeric else (None, start)


def _pairs_at(lines: list[str], start: int):
    i = start
    rows: list[tuple[str, str]] = []
    while i < len(lines):
        match = _LABEL_VALUE.match(lines[i])
        if not match:
            break
        rows.append((match.group(1).strip(), match.group(2).strip()))
        i += 1
    if len(rows) < 2:
        return None, start
    return (("label", "value"), tuple(rows)), i
