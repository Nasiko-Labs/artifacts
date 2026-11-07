"""Pure: find the pasted table in a message and split it into a header and rows of strings.

Accepts CSV, TSV, semicolon- or pipe-separated text, markdown tables (outer pipes and the `---` rule row
are dropped) and another agent's envelope (`items[]` of flat objects). The text around the table is the
user's request (metric name, events, min_n). Caps: 5,000 data rows (more → the first 5,000, with a note).
A paste cut mid-row at the chat's size limit loses its last partial row, with a note.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass

MAX_ROWS = 5_000
DELIMITERS = (",", "\t", ";", "|")
_FENCE_LINE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
_MD_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_FENCED_JSON = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)
_NUMBER_CELL = re.compile(r"^\(?[-−+]?[$€£¥]?\s*\d[\d,]*(?:\.\d+)?\s*%?\)?$")

csv.field_size_limit(1_000_000)


@dataclass(frozen=True)
class Grid:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    question: str = ""          # the message text outside the table
    notes: tuple[str, ...] = ()
    source_rows: int = 0


def parse_number(cell: str) -> float | None:
    """'1,234' → 1234; '$1,234.50' → 1234.5; '4.8%' → 4.8; '(120)' → -120; '' or text → None."""
    text = (cell or "").strip().replace(" ", "")
    if not text or not _NUMBER_CELL.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    clean = re.sub(r"[()$€£¥,%+]", "", text).replace("−", "-")
    try:
        value = float(clean)
    except ValueError:
        return None
    return -value if negative else value


def find_grid(text: str) -> Grid | None:
    """The table in `text`, or None when there is no block of 2+ columns and 2+ lines."""
    enveloped = from_envelope(text)
    if enveloped is not None:
        return enveloped
    lines = [_unpipe(line) for line in (text or "").splitlines()
             if not _FENCE_LINE.match(line) and not _MD_RULE.match(line)]
    best: tuple[int, int, str] = (0, 0, ",")
    for delimiter in DELIMITERS:
        start, end = _block(lines, delimiter)
        if end - start > best[1] - best[0]:
            best = (start, end, delimiter)
    start, end, delimiter = best
    if end - start < 2:
        return None
    question = "\n".join([*lines[:start], *lines[end:]]).strip()
    return _parse("\n".join(lines[start:end]), delimiter, question)


def _unpipe(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
        return stripped[1:-1]
    return line


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


def _parse(table_text: str, delimiter: str, question: str) -> Grid | None:
    records = [[c.strip() for c in r] for r in csv.reader(io.StringIO(table_text), delimiter=delimiter)
               if any(c.strip() for c in r)]
    if len(records) < 2:
        return None
    header = _clean_header(records[0])
    width = len(header)
    notes: list[str] = []
    body = records[1:]
    if body and len(body[-1]) < width:
        body = body[:-1]
        notes.append("The last row had fewer fields than the header (the paste was probably cut), so I dropped it.")
    rows = [tuple((list(r) + [""] * width)[:width]) for r in body]
    return _capped(header, rows, notes, question)


def _capped(header: tuple[str, ...], rows: list[tuple[str, ...]], notes: list[str], question: str) -> Grid:
    if len(rows) > MAX_ROWS:
        notes.append(f"The table has {len(rows):,} rows; I read the first {MAX_ROWS:,}.")
    return Grid(header, tuple(rows[:MAX_ROWS]), question, tuple(notes), len(rows))


def _clean_header(raw: list[str]) -> tuple[str, ...]:
    seen: dict[str, int] = {}
    names = []
    for n, name in enumerate(raw, start=1):
        clean = " ".join(name.split()) or f"column_{n}"
        count = seen.get(clean.casefold(), 0)
        seen[clean.casefold()] = count + 1
        names.append(clean if count == 0 else f"{clean}_{count + 1}")
    return tuple(names)


def from_envelope(text: str) -> Grid | None:
    """Another agent's envelope: `items[]` of flat objects (e.g. {segment, before, after}) become rows."""
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
        header += [str(k) for k in item if str(k) not in header and not isinstance(item[k], (dict, list))]
    rows = [tuple("" if item.get(k) is None else str(item.get(k)) for k in header) for item in items]
    question = str(data.get("metric") or "") if isinstance(data.get("metric"), str) else ""
    return _capped(_clean_header(header), rows, [], question)
