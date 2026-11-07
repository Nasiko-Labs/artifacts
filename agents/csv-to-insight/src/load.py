"""Pure: find the table in what the user sent, sniff its delimiter and parse it, honouring quotes.

Caps: 200,000 rows and 200 columns (more → the first N, with a note). A paste cut mid-row at the chat's
size limit loses its last partial row (fewer fields than the header), with a note. Another agent's envelope
(`items[]` of flat objects) is read as rows. Every cell stays a string here; profile.py converts.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass

MAX_ROWS = 200_000
MAX_COLS = 200
DELIMITERS = (",", "\t", ";", "|")
DELIMITER_NAMES = {",": "comma", "\t": "tab", ";": "semicolon", "|": "pipe"}
_FENCE_LINE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)

csv.field_size_limit(1_000_000)


@dataclass(frozen=True)
class Loaded:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    notes: tuple[str, ...] = ()
    source_rows: int = 0          # data rows seen before any cap
    source_cols: int = 0
    delimiter: str = ","

    @property
    def capped(self) -> bool:
        return self.source_rows > len(self.rows) or self.source_cols > len(self.header)


def split_message(text: str) -> tuple[str, str, str]:
    """(question, table_text, delimiter). table_text is "" when no table of 2+ columns is found."""
    lines = [line for line in (text or "").splitlines() if not _FENCE_LINE.match(line)]
    best: tuple[int, int, str] = (0, 0, ",")
    for delimiter in DELIMITERS:
        start, end = _block(lines, delimiter)
        if end - start > best[1] - best[0]:
            best = (start, end, delimiter)
    start, end, delimiter = best
    if end - start < 2:
        return (text or "").strip(), "", ","
    question = "\n".join([*lines[:start], *lines[end:]]).strip()
    return question, "\n".join(lines[start:end]), delimiter


def _fields(line: str, delimiter: str) -> int:
    return len(next(csv.reader([line], delimiter=delimiter), []))


def _block(lines: list[str], delimiter: str) -> tuple[int, int]:
    """The longest run of lines with the same field count (≥ 2), plus one trailing shorter line."""
    best = (0, 0)
    i = 0
    while i < len(lines):
        width = _fields(lines[i], delimiter) if lines[i].strip() else 0
        if width < 2:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() and _fields(lines[j], delimiter) == width:
            j += 1
        if j < len(lines) and lines[j].strip() and 1 <= _fields(lines[j], delimiter) < width and j > i + 1:
            j += 1  # a partial last row, dropped with a note when parsed
        if j - i > best[1] - best[0]:
            best = (i, j)
        i = max(j, i + 1)
    return best


def parse_table(table_text: str, delimiter: str = ",") -> Loaded:
    records = [r for r in csv.reader(io.StringIO(table_text), delimiter=delimiter) if any(c.strip() for c in r)]
    if not records:
        return Loaded((), ())
    header = _clean_header(records[0])
    width = len(header)
    notes: list[str] = []
    body = records[1:]
    if body and len(body[-1]) < width:
        body = body[:-1]
        notes.append("The last row had fewer fields than the header (the paste was probably cut), so I dropped it.")
    uneven = sum(1 for r in body if len(r) != width)
    if uneven:
        notes.append(f"{uneven:,} rows had a different number of fields than the header; I padded or trimmed them.")
    rows = [tuple((list(r) + [""] * width)[:width]) for r in body]
    return _capped(header, rows, notes, delimiter)


def _capped(header: tuple[str, ...], rows: list[tuple[str, ...]], notes: list[str], delimiter: str) -> Loaded:
    source_rows, source_cols = len(rows), len(header)
    if source_cols > MAX_COLS:
        notes.append(f"The table has {source_cols:,} columns; I read the first {MAX_COLS}.")
        header, rows = header[:MAX_COLS], [r[:MAX_COLS] for r in rows]
    if source_rows > MAX_ROWS:
        notes.append(f"The table has {source_rows:,} rows; I read the first {MAX_ROWS:,}.")
        rows = rows[:MAX_ROWS]
    return Loaded(header, tuple(rows), tuple(notes), source_rows, source_cols, delimiter)


def _clean_header(raw: list[str]) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    names = []
    for n, name in enumerate(raw, start=1):
        clean = " ".join(name.split()) or f"column_{n}"
        count = seen.get(clean.casefold(), 0)
        seen[clean.casefold()] = count + 1
        names.append(clean if count == 0 else f"{clean}_{count + 1}")
    return tuple(names)


def from_envelope(text: str) -> Loaded | None:
    """Another agent's envelope: `items[]` of flat objects become rows. None when `text` is not one."""
    body = (text or "").strip()
    fenced = _FENCED_JSON.match(body)
    if fenced:
        body = fenced.group(1)
    if not body.startswith("{"):
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
        return None
    header: list[str] = []
    for item in items:
        header += [str(k) for k in item if str(k) not in header]
    rows = [tuple(_cell(item.get(k)) for k in header) for item in items]
    return _capped(_clean_header(header), rows, [], ",")


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
