"""Pure: cell parsing and column typing (the plan's `types.py`; renamed so it never shadows the standard
library module of that name).

Numbers: currency symbols, thousands separators, `%` and spaces are stripped; parentheses mean negative.
Dates: ISO (`2026-10-05`, with or without a time), or day/month/year vs month/day/year, decided per column
by any part above 12; a column ambiguous both ways reads as month/day/year and says so.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

ColType = Literal["number", "date", "text"]
DateOrder = Literal["iso", "dmy", "mdy"]

SAMPLE_SIZE = 200
TYPE_SHARE = 0.8  # share of sampled non-empty cells that must parse for a column to take that type
CURRENCY_SYMBOLS = "$€£¥₹"
_STRIP = re.compile(rf"[{re.escape(CURRENCY_SYMBOLS)},%\s]")
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")
_SLASHED = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$")


@dataclass(frozen=True)
class Column:
    name: str
    type: ColType
    date_order: DateOrder | None = None
    ambiguous_dates: bool = False

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type}


def is_blank(cell: object) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def parse_number(cell: object) -> Decimal | None:
    if isinstance(cell, bool) or cell is None:
        return None
    if isinstance(cell, (int, float)):
        return Decimal(str(cell))
    text = str(cell).strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = _STRIP.sub("", text)
    if not _NUMBER.match(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return -value if negative else value


def currency_of(cell: object) -> str | None:
    text = str(cell or "")
    return next((s for s in CURRENCY_SYMBOLS if s in text), None)


def parse_date(cell: object, order: DateOrder | None = None) -> date | None:
    text = str(cell or "").strip()
    iso = _ISO.match(text)
    if iso:
        return _make(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    slashed = _SLASHED.match(text)
    if not slashed:
        return None
    a, b, year = int(slashed.group(1)), int(slashed.group(2)), int(slashed.group(3))
    year = year + 2000 if year < 100 else year
    day, month = (a, b) if order == "dmy" else (b, a)
    return _make(year, month, day)


def infer(name: str, cells: Sequence[object]) -> Column:
    sample = [c for c in cells if not is_blank(c)][:SAMPLE_SIZE]
    if not sample:
        return Column(name, "text")
    if _share(sample, lambda c: parse_number(c) is not None) >= TYPE_SHARE:
        return Column(name, "number")
    if all(isinstance(c, str) and _ISO.match(c.strip()) for c in sample):
        return Column(name, "date", "iso")
    slashed = [_SLASHED.match(str(c).strip()) for c in sample]
    if _share(sample, lambda c: _ISO.match(str(c).strip()) or _SLASHED.match(str(c).strip())) < TYPE_SHARE:
        return Column(name, "text")
    order, ambiguous = _order([m for m in slashed if m])
    if order is None:
        return Column(name, "text")
    return Column(name, "date", order, ambiguous)


def _order(matches: Sequence[re.Match]) -> tuple[DateOrder | None, bool]:
    first_big = any(int(m.group(1)) > 12 for m in matches)
    second_big = any(int(m.group(2)) > 12 for m in matches)
    if first_big and second_big:
        return None, False  # no single reading fits every cell
    if first_big:
        return "dmy", False
    if second_big:
        return "mdy", False
    return "mdy", bool(matches)


def _share(sample: Sequence[object], test) -> float:
    return sum(1 for c in sample if test(c)) / len(sample)


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
