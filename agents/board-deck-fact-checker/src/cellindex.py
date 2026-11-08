"""Pure: turn the tabs read this turn into a cell index. Each numeric cell gets its address (`Model!C14`),
value, row label (the nearest text cell to its left), column label (the nearest text or date cell above
it), the scale its labels state (`($000)`, `in thousands`, `$M`, `millions`) and a `cell_id`.

A tab is cut at `MAX_CELLS_PER_TAB` cells; the cut is reported, never silent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import figures
from records import Cell

MAX_CELLS_PER_TAB = 2_000

_SCALE_LABELS: tuple[tuple[re.Pattern, float], ...] = (
    (re.compile(r"\(\s*[$€£]?\s*0{3}s?\s*\)|'000s?\b|\bin thousands\b|\bthousands\b|\([$€£]?\s*k\s*\)|[$€£]\s?k\b",
                re.IGNORECASE), figures.THOUSAND),
    (re.compile(r"\(\s*[$€£]?\s*mm?\s*\)|[$€£]\s?mm?\b|\bin millions\b|\bmillions\b", re.IGNORECASE),
     figures.MILLION),
    (re.compile(r"\(\s*[$€£]?\s*bn?\s*\)|[$€£]\s?bn\b|\bin billions\b|\bbillions\b", re.IGNORECASE),
     figures.BILLION),
)
_A1 = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)")
_SAFE_TAB = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class Tab:
    spreadsheet: str
    tab: str
    rows: tuple[tuple[str, ...], ...]
    url: str = ""
    origin_row: int = 1   # sheet row of rows[0]
    origin_col: int = 1   # sheet column of rows[n][0]
    cut: bool = False


def column_letters(index: int) -> str:
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def column_index(letters: str) -> int:
    index = 0
    for ch in letters.upper():
        index = index * 26 + ord(ch) - 64
    return index


def origin(range_text: str) -> tuple[str, int, int]:
    """`Metrics!B2:F20` -> ("Metrics", 2, 2); a bare tab name -> (name, 1, 1)."""
    tab, bang, cells = (range_text or "").rpartition("!")
    if not bang:
        tab, cells = ("", range_text) if _A1.match(range_text or "") else (range_text or "", "")
    tab = tab.strip().strip("'")
    found = _A1.match(cells.strip())
    if not found:
        return tab, 1, 1
    return tab, int(found.group(2)), column_index(found.group(1))


def cap(rows: Sequence[Sequence[object]]) -> tuple[tuple[tuple[str, ...], ...], bool]:
    """Rows as strings, cut once `MAX_CELLS_PER_TAB` cells are reached."""
    kept: list[tuple[str, ...]] = []
    count = 0
    for row in rows:
        cells = tuple("" if c is None else str(c) for c in row)
        if count + len(cells) > MAX_CELLS_PER_TAB:
            return tuple(kept), True
        kept.append(cells)
        count += len(cells)
    return tuple(kept), False


def label_scale(text: str) -> float | None:
    return next((scale for pattern, scale in _SCALE_LABELS if pattern.search(text or "")), None)


def _is_label(text: str, top_row: bool) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    parsed = figures.parse(stripped, whole=True)
    if parsed is None:
        return True
    return top_row and parsed.unit == "count" and parsed.precision == 1 and 1900 <= parsed.value <= 2100 \
        and "," not in stripped


def _row_label(row: Sequence[str], col: int, top_row: bool) -> str:
    return next((row[c].strip() for c in range(col - 1, -1, -1) if _is_label(row[c], top_row)), "")


def _col_label(rows: Sequence[Sequence[str]], r: int, col: int) -> str:
    for above in range(r - 1, -1, -1):
        row = rows[above]
        if col < len(row) and _is_label(row[col], above == 0):
            return row[col].strip()
    return ""


def address(tab: str, row: int, col: int) -> str:
    name = tab if _SAFE_TAB.match(tab) else f"'{tab}'"
    return f"{name}!{column_letters(col)}{row}"


def index_tab(tab: Tab, start: int = 0) -> tuple[Cell, ...]:
    corner = tab.rows[0][0] if tab.rows and tab.rows[0] else ""
    cells: list[Cell] = []
    for r, row in enumerate(tab.rows):
        for c, text in enumerate(row):
            if _is_label(text, r == 0) or not text.strip():
                continue
            parsed = figures.parse(text, whole=True)
            row_label, col_label = _row_label(row, c, r == 0), _col_label(tab.rows, r, c)
            cells.append(_cell(tab, f"c{start + len(cells) + 1}", tab.origin_row + r, tab.origin_col + c,
                               text.strip(), parsed, row_label, col_label, corner))
    return tuple(cells)


def _cell(tab: Tab, cell_id: str, row: int, col: int, text: str, parsed: figures.Number, row_label: str,
          col_label: str, corner: str) -> Cell:
    own_scale = parsed.scale != 1
    scale, note = 1.0, ""
    if not own_scale and parsed.unit != "percent":
        for label in (row_label, col_label, corner, tab.tab):
            found = label_scale(label)
            if found:
                scale, note = found, f"scale from label '{label}'"
                break
    return Cell(cell_id=cell_id, spreadsheet=tab.spreadsheet, tab=tab.tab, address=address(tab.tab, row, col),
                value=parsed.absolute, text=text, row_label=row_label, col_label=col_label, url=tab.url,
                scale=scale, scale_note=note, scale_known=own_scale or bool(note), percent=parsed.unit == "percent")


def build(tabs: Iterable[Tab]) -> tuple[Cell, ...]:
    cells: tuple[Cell, ...] = ()
    for tab in tabs:
        cells = (*cells, *index_tab(tab, len(cells)))
    return cells
