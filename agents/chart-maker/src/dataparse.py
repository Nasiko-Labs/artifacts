"""Pure: pasted text -> a table of strings, and cell text -> numbers.

Formats: a JSON array of objects (or an object of arrays, or an envelope with tabular `items`), a
markdown table, `label: value` lines, and CSV/TSV/semicolon text. The first row is a header when any
of its cells is not a number while that column holds numbers below it."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass

_CURRENCY = "$€£¥₹"
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$", re.IGNORECASE)
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")
_LABEL_VALUE = re.compile(r"^\s*[-*]?\s*([^:=|\t]{1,80}?)\s*[:=]\s*(\S[^:=]*?)\s*$")
_MD_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_INLINE_ROWS = re.compile(r"\s+/\s+|\s*;\s+")


@dataclass(frozen=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    had_header: bool = True


def parse_number(cell: str) -> float | None:
    """`$1,200` -> 1200, `12%` -> 12, `(300)` -> -300, `1.2e3` -> 1200; None when not a number."""
    text = (cell or "").strip().replace("−", "-").replace(" ", "").replace(" ", "")
    negative = text.startswith("(") and text.endswith(")")
    text = text[1:-1] if negative else text
    sign = ""
    if text[:1] in "+-" and len(text) > 1:
        sign, text = text[0], text[1:]
    text = text.strip(_CURRENCY).removesuffix("%").strip(_CURRENCY)
    if _THOUSANDS.match(text):
        text = text.replace(",", "")
    if not _NUMBER.match(text):
        return None
    value = float(sign + text)
    return -value if negative else value


def parse_table(text: str) -> Table | None:
    """The table in `text`, or None when no format yields >= 1 row and >= 2 columns."""
    body = (text or "").strip()
    if not body:
        return None
    for reader in (_from_json, _from_markdown, _from_label_values, _from_delimited):
        table = reader(body)
        if table is not None and table.rows and len(table.columns) >= 2:
            return table
    return None


def _from_json(body: str) -> Table | None:
    if body[0] not in "[{":
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]  # a previous step's envelope: its tabular items are the data
    if isinstance(data, dict) and data and all(isinstance(v, list) for v in data.values()):
        columns = tuple(str(k) for k in data)
        length = max(len(v) for v in data.values())
        rows = [[_cell(v[i]) if i < len(v) else "" for v in data.values()] for i in range(length)]
        return Table(columns, tuple(tuple(r) for r in rows))
    if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
        return None
    columns: list[str] = []
    for row in data:
        columns += [str(k) for k in row if str(k) not in columns]
    rows = tuple(tuple(_cell(row.get(c)) for c in columns) for row in data)
    return Table(tuple(columns), rows)


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _from_markdown(body: str) -> Table | None:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) < 2 or not all("|" in ln for ln in lines):
        return None
    cells = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines if not _MD_SEPARATOR.match(ln)]
    return _with_header(cells)


def _from_label_values(body: str) -> Table | None:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    matches = [_LABEL_VALUE.match(ln) for ln in lines]
    if not lines or not all(m and parse_number(m.group(2)) is not None for m in matches):
        return None
    rows = tuple((m.group(1).strip(), m.group(2).strip()) for m in matches)
    return Table(("Category", "Value"), rows, had_header=False)


def _from_delimited(body: str) -> Table | None:
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if len(lines) == 1 and _INLINE_ROWS.search(lines[0]):
        lines = [p for p in _INLINE_ROWS.split(lines[0]) if p.strip()]
    delimiter = _delimiter(lines)
    if delimiter is None:
        return None
    cells = [[c.strip() for c in row] for row in csv.reader(lines, delimiter=delimiter)]
    return _with_header(cells)


def _delimiter(lines: list[str]) -> str | None:
    for delimiter in ("\t", ";", ","):
        counts = {ln.count(delimiter) for ln in lines}
        if min(counts) >= 1:
            return delimiter
    return None


def _with_header(cells: list[list[str]]) -> Table | None:
    width = max((len(r) for r in cells), default=0)
    if width < 2:
        return None
    rows = [tuple((r + [""] * width)[:width]) for r in cells]
    if _is_header(rows):
        names = [c or f"Column {i + 1}" for i, c in enumerate(rows[0])]
        return Table(tuple(names), tuple(rows[1:]))
    names = ["Category"] + (["Value"] if width == 2 else [f"Series {i}" for i in range(1, width)])
    return Table(tuple(names), tuple(rows), had_header=False)


def _is_header(rows: list[tuple[str, ...]]) -> bool:
    if len(rows) < 2:
        return all(parse_number(c) is None for c in rows[0])
    first, rest = rows[0], rows[1:]
    for i, cell in enumerate(first):
        numeric_below = any(parse_number(r[i]) is not None for r in rest)
        if numeric_below and parse_number(cell) is None:
            return True
    return all(parse_number(c) is None for c in first) and any(parse_number(c) is not None for r in rest for c in r)
