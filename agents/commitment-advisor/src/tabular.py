"""Pure: CSV reading shared by the usage and commitments blocks. Delimiter sniffed from the header line,
a BOM stripped, headers normalised (case, spaces, hyphens), numbers and UTC timestamps parsed leniently."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

DELIMITERS = (",", ";", "\t", "|")
_HEADER_SEP = re.compile(r"[\s\-]+")
_MONEY = re.compile(r"[$€£,\s]")


@dataclass(frozen=True)
class Table:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def column(self, *names: str) -> int | None:
        return next((self.headers.index(n) for n in names if n in self.headers), None)


def norm(header: str) -> str:
    return _HEADER_SEP.sub("_", (header or "").replace("﻿", "").strip().casefold())


def delimiter_of(line: str) -> str:
    counts = {d: line.count(d) for d in DELIMITERS}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def read(text: str) -> Table:
    lines = [x for x in (text or "").replace("﻿", "").replace("\r\n", "\n").split("\n") if x.strip()]
    if not lines:
        return Table((), ())
    reader = csv.reader(lines, delimiter=delimiter_of(lines[0]))
    rows = [tuple(cell.strip() for cell in row) for row in reader]
    return Table(tuple(norm(h) for h in rows[0]), tuple(rows[1:]))


def cell(row: tuple[str, ...], index: int | None) -> str:
    return row[index] if index is not None and index < len(row) else ""


def number(text: str) -> float | None:
    raw = _MONEY.sub("", text or "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def timestamp(text: str) -> datetime | None:
    raw = (text or "").strip().replace("/", "-")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace(" UTC", "").removesuffix("Z"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def day(text: str) -> date | None:
    parsed = timestamp(text)
    return parsed.date() if parsed else None
