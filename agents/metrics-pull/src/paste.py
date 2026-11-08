"""Pure: a table pasted in the message (a markdown table, tab-separated, or comma-separated lines), split
from the request text around it. The same engine runs on it as on a sheet; row numbers are the line's
position in the pasted table (header = 1)."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from headers import SheetRow

MIN_LINES = 2  # header + at least one row
_MD_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


@dataclass(frozen=True)
class Pasted:
    request: str               # the message without the table
    rows: tuple[SheetRow, ...]
    text: str                  # the table as pasted (for the verbatim check)


def find(message: str) -> Pasted | None:
    lines = (message or "").splitlines()
    best: tuple[int, int, str] | None = None
    start = 0
    while start < len(lines):
        delim = _delimiter(lines[start])
        if delim is None:
            start += 1
            continue
        end = start
        while end + 1 < len(lines) and _delimiter(lines[end + 1]) == delim:
            end += 1
        if end - start + 1 >= MIN_LINES and (best is None or end - start > best[1] - best[0]):
            best = (start, end, delim)
        start = end + 1
    if best is None:
        return None
    first, last, delim = best
    block = lines[first:last + 1]
    rows = _rows(block, delim)
    if len(rows) < MIN_LINES or max(len(r.cells) for r in rows) < 2:
        return None
    request = "\n".join((*lines[:first], *lines[last + 1:])).strip()
    return Pasted(request, rows, "\n".join(block))


def _delimiter(line: str) -> str | None:
    text = line.strip()
    if text.startswith("|") and text.count("|") >= 3:
        return "|"
    if text.count("\t") >= 1:
        return "\t"
    if text.count(",") >= 1 and not text.endswith((".", "?", "!")) and len(text.split()) <= text.count(",") * 4 + 4:
        return ","
    return None


def _rows(block: list[str], delim: str) -> tuple[SheetRow, ...]:
    if delim == "|":
        cells = [[c.strip() for c in line.strip().strip("|").split("|")] for line in block
                 if not _MD_SEPARATOR.match(line.strip())]
    else:
        reader = csv.reader(io.StringIO("\n".join(block)), delimiter=delim)
        cells = [[c.strip() for c in row] for row in reader if row]
    return tuple(SheetRow(i + 1, tuple(row)) for i, row in enumerate(cells))
