"""Pure: find the table in what the user sent and split it into rows of cells, separate from the instruction
around it. Forms read: a JSON array of objects, a markdown table, and delimited lines (tab, comma, pipe,
semicolon, or two-plus spaces). The longest consistent block wins; everything else is the instruction."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass

DELIMITERS = ("\t", ",", "|", ";", "  ")
MIN_LINES = 2          # header + one row; fewer rows are refused later with a message
_FENCE_LINE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
_MD_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$")
_SPACES = re.compile(r"\s{2,}")

csv.field_size_limit(1_000_000)


@dataclass(frozen=True)
class Found:
    instruction: str
    records: tuple[tuple[str, ...], ...]   # first record is the header candidate
    form: str                              # json | markdown | tab | comma | pipe | semicolon | spaces


def find_table(text: str) -> Found | None:
    lines = [line for line in (text or "").splitlines() if not _FENCE_LINE.match(line)]
    return _json_table(text or "") or _markdown(lines) or _delimited(lines)


def records_from_json(data: object) -> tuple[tuple[str, ...], ...] | None:
    """A list of flat objects (or a list of lists, first one the header) as records."""
    if not isinstance(data, list) or len(data) < 1:
        return None
    if all(isinstance(r, dict) for r in data):
        header: list[str] = []
        for row in data:
            header += [str(k) for k in row if str(k) not in header]
        return (tuple(header), *(tuple(_cell(r.get(k)) for k in header) for r in data))
    if all(isinstance(r, list) for r in data) and len(data) >= 2:
        return tuple(tuple(_cell(c) for c in r) for r in data)
    return None


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def _json_table(text: str) -> Found | None:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    records = records_from_json(data)
    if records is None or len(records) < MIN_LINES:
        return None
    instruction = (text[:start] + "\n" + text[end + 1:]).replace("```json", "").replace("```", "").strip()
    return Found(instruction, records, "json")


def _markdown(lines: list[str]) -> Found | None:
    best: tuple[int, int] = (0, 0)
    i = 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip().startswith("|"):
            j += 1
        if j - i > best[1] - best[0]:
            best = (i, j)
        i = j
    start, end = best
    rows = [_md_cells(line) for line in lines[start:end] if not _MD_RULE.match(line.strip())]
    if len(rows) < MIN_LINES or max(len(r) for r in rows) < 2:
        return None
    width = len(rows[0])
    records = tuple(tuple((r + [""] * width)[:width]) for r in rows)
    return Found(_rest(lines, start, end), records, "markdown")


def _md_cells(line: str) -> list[str]:
    body = line.strip()
    body = body.removeprefix("|")
    body = body.removesuffix("|")
    return [c.strip() for c in body.split("|")]


def _split(line: str, delimiter: str) -> list[str]:
    if delimiter == "  ":
        return [c.strip() for c in _SPACES.split(line.strip())]
    return [c.strip() for c in next(csv.reader([line], delimiter=delimiter, skipinitialspace=True), [])]


def _delimited(lines: list[str]) -> Found | None:
    best: tuple[int, int, int, str] = (0, 0, 0, ",")
    for delimiter in DELIMITERS:
        start, end, width = _block(lines, delimiter)
        if (end - start, width) > (best[1] - best[0], best[2]):
            best = (start, end, width, delimiter)
    start, end, width, delimiter = best
    if end - start < MIN_LINES:
        return None
    records = tuple(tuple(_split(line, delimiter)) for line in lines[start:end])
    names = {"\t": "tab", ",": "comma", "|": "pipe", ";": "semicolon", "  ": "spaces"}
    return Found(_rest(lines, start, end), records, names[delimiter])


def _block(lines: list[str], delimiter: str) -> tuple[int, int, int]:
    """The longest run of non-blank lines that split into the same number (≥ 2) of cells."""
    best = (0, 0, 0)
    i = 0
    while i < len(lines):
        width = len(_split(lines[i], delimiter)) if lines[i].strip() else 0
        if width < 2:
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() and len(_split(lines[j], delimiter)) == width:
            j += 1
        if j - i > best[1] - best[0]:
            best = (i, j, width)
        i = j
    return best


def _rest(lines: list[str], start: int, end: int) -> str:
    return "\n".join([*lines[:start], *lines[end:]]).strip()
