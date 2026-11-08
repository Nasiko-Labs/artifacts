"""Pure: the header map. The header is the first row, unless it is mostly numbers, in which case it is the
first of the next rows whose non-empty cells are at least half text. Empty header cells get a positional
name; duplicate names get a `_2`, `_3` suffix. Each column is typed in code (coltypes.infer)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import coltypes
from coltypes import Column

HEADER_SCAN_ROWS = 10
TEXT_SHARE = 0.5


@dataclass(frozen=True)
class SheetRow:
    """One row as read: its sheet row number (1-based) and its cells as text or numbers."""

    number: int
    cells: tuple[object, ...]


@dataclass(frozen=True)
class HeaderMap:
    columns: tuple[Column, ...]
    rows: tuple[SheetRow, ...]          # data rows only, cells padded to the header width
    header_row: int = 1
    renamed: tuple[str, ...] = field(default=())   # duplicate or empty header cells that were renamed

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def index(self, name: str) -> int:
        return self.names.index(name)

    def column(self, name: str) -> Column:
        return self.columns[self.index(name)]


def build(rows: Sequence[SheetRow]) -> HeaderMap | None:
    """None when there is no row with any value."""
    filled = [r for r in rows if any(not coltypes.is_blank(c) for c in r.cells)]
    if not filled:
        return None
    pos = _header_position(filled)
    header = filled[pos]
    data = filled[pos + 1:]
    width = max(len(r.cells) for r in (header, *data))
    names, renamed = _names(header.cells, width)
    padded = tuple(SheetRow(r.number, _pad(r.cells, width)) for r in data)
    columns = tuple(coltypes.infer(n, [r.cells[i] for r in padded]) for i, n in enumerate(names))
    return HeaderMap(columns, padded, header.number, renamed)


def _header_position(rows: Sequence[SheetRow]) -> int:
    for pos, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        values = [c for c in row.cells if not coltypes.is_blank(c)]
        texty = [c for c in values if coltypes.parse_number(c) is None]
        if values and len(texty) / len(values) >= TEXT_SHARE:
            return pos
    return 0


def _names(cells: Sequence[object], width: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names: list[str] = []
    renamed: list[str] = []
    for i in range(width):
        raw = cells[i] if i < len(cells) else ""
        base = str(raw).strip() if not coltypes.is_blank(raw) else f"column_{i + 1}"
        name, n = base, 1
        while name.casefold() in (x.casefold() for x in names):
            n += 1
            name = f"{base}_{n}"
        if name != str(raw).strip():
            renamed.append(name)
        names.append(name)
    return tuple(names), tuple(renamed)


def _pad(cells: Sequence[object], width: int) -> tuple[object, ...]:
    return tuple(cells[:width]) + ("",) * (width - len(cells))
