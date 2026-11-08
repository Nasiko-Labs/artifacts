"""Pure: the inventory as a plain table (a header and text rows) from sheet rows, pasted text (markdown pipes,
tabs or CSV) or a previous step's envelope. Typing the columns is headers.py's job."""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_ROWS = 2000
MAX_COLS = 40
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
_RULE_CELL = re.compile(r"\s*:?-{2,}:?\s*")


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]     # data rows, padded to the header width
    source: str                            # "sheet:<title>" | "pasted" | "envelope"
    truncated: int = 0                     # rows beyond MAX_ROWS that were not read

    def to_dict(self) -> dict:
        return {"header": list(self.header), "rows": [list(r) for r in self.rows], "source": self.source,
                "truncated": self.truncated}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Table | None:
        if not data or not data.get("header"):
            return None
        return Table(tuple(str(h) for h in data["header"]), tuple(tuple(str(c) for c in r) for r in data.get("rows") or ()),
                     str(data.get("source") or ""), int(data.get("truncated") or 0))


def from_rows(rows: Sequence[Sequence[Any]], source: str) -> Table | None:
    """The first row with two or more non-empty cells is the header; blank rows are skipped."""
    clean = [[cell_text(c) for c in row][:MAX_COLS] for row in rows if isinstance(row, (list, tuple))]
    for at, row in enumerate(clean):
        if sum(1 for c in row if c) >= 2:
            width = len(row)
            body = [tuple((r + [""] * width)[:width]) for r in clean[at + 1:] if any(c for c in r)]
            if not body:
                return None
            header = tuple(h or f"Column {i + 1}" for i, h in enumerate(row))
            return Table(header, tuple(body[:MAX_ROWS]), source, max(0, len(body) - MAX_ROWS))
    return None


def from_text(text: str, source: str = "pasted") -> Table | None:
    """A pasted table: markdown pipes, tabs, or CSV. None when no table is there."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    for splitter in (_pipe_rows, _tab_rows, _csv_rows):
        rows = splitter(lines)
        if len(rows) >= 2:
            found = from_rows(rows, source)
            if found is not None:
                return found
    return None


def from_envelope_text(text: str) -> Table | None:
    """A previous step's envelope: `table` ({header|columns, rows}, row objects or lists) or `dashboards`."""
    data = _envelope(text)
    if data is None:
        return None
    for key in ("table", "dashboards", "items"):
        rows = _rows(data.get(key))
        if rows:
            found = from_rows(rows, "envelope")
            if found is not None:
                return found
    return None


def cell_text(cell: Any) -> str:
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return str(cell).lower()
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    if isinstance(cell, (list, tuple)):
        return "; ".join(cell_text(c) for c in cell)
    if isinstance(cell, Mapping):
        return json.dumps(dict(cell), default=str)
    return " ".join(str(cell).split())


def _pipe_rows(lines: list[str]) -> list[list[str]]:
    rows = [ln.strip().strip("|").split("|") for ln in lines if ln.count("|") >= 2]
    return [[c.strip() for c in r] for r in rows if not all(_RULE_CELL.fullmatch(c) for c in r)]


def _tab_rows(lines: list[str]) -> list[list[str]]:
    return [[c.strip() for c in ln.split("\t")] for ln in lines if "\t" in ln]


def _csv_rows(lines: list[str]) -> list[list[str]]:
    candidates = [ln for ln in lines if ln.count(",") >= 1]
    rows = list(csv.reader(io.StringIO("\n".join(candidates))))
    widths = Counter(len(r) for r in rows if len(r) >= 2)
    if not widths:
        return []
    width = widths.most_common(1)[0][0]  # the table's width, not a stray sentence's
    return [[c.strip() for c in r] for r in rows if len(r) == width]


def _rows(raw: Any) -> list[list[Any]]:
    if isinstance(raw, Mapping):
        header = raw.get("columns") or raw.get("header")
        body = raw.get("rows")
        if isinstance(header, list) and isinstance(body, list):
            names = [h.get("name") if isinstance(h, Mapping) else h for h in header]
            return [names, *[[r.get(n) for n in names] if isinstance(r, Mapping) else list(r) for r in body
                             if isinstance(r, (Mapping, list))]]
    if isinstance(raw, list) and raw:
        if all(isinstance(r, Mapping) for r in raw):
            names = list(dict.fromkeys(k for r in raw for k in r))
            return [names, *[[r.get(k) for k in names] for r in raw]]
        if all(isinstance(r, list) for r in raw):
            return [list(r) for r in raw]
    return []


def _envelope(text: str) -> Mapping[str, Any] | None:
    body = (text or "").strip()
    fenced = _FENCED_JSON.search(body)
    candidate = fenced.group(1) if fenced else body[body.find("{"):] if "{" in body else ""
    if not candidate.startswith("{"):
        return None
    try:
        data = json.loads(candidate)
    except ValueError:
        return None
    return data if isinstance(data, Mapping) else None
