"""Pure: date cells parsed in code (the metrics-pull method, written here, not imported).

Accepted: ISO (`2026-11-03`, with or without a time), day/month/year vs month/day/year decided per column by
any part above 12 (a column ambiguous both ways reads as month/day/year), and month names (`3 Nov`,
`Nov 3, 2026`, `November 3`). A month name without a year takes the turn's year. Anything else is
unparsable and becomes a `bad_date` note, never an event."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Literal

DateOrder = Literal["dmy", "mdy"]

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_MONTH_RE = "|".join(f"{m[:3]}(?:{m[3:]})?" for m in _MONTHS) + "|sept"
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$", re.IGNORECASE)
_SLASHED = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$")
_DAY_MONTH = re.compile(rf"^(?:[a-z]{{3,9}},?\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RE})\.?,?(?:\s+(\d{{4}}))?$")
_MONTH_DAY = re.compile(rf"^(?:[a-z]{{3,9}},?\s+)?({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?(?:\s+(\d{{4}}))?$")


def column_order(cells: Sequence[str]) -> DateOrder:
    matches = [m for m in (_SLASHED.match(c.strip()) for c in cells) if m]
    first_big = any(int(m.group(1)) > 12 for m in matches)
    second_big = any(int(m.group(2)) > 12 for m in matches)
    return "dmy" if first_big and not second_big else "mdy"


def parse(cell: str, year: int, order: DateOrder = "mdy") -> date | None:
    text = (cell or "").strip().casefold()
    if not text:
        return None
    iso = _ISO.match(text)
    if iso:
        return _make(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    slashed = _SLASHED.match(text)
    if slashed:
        a, b, y = int(slashed.group(1)), int(slashed.group(2)), int(slashed.group(3))
        y = y + 2000 if y < 100 else y
        day, month = (a, b) if order == "dmy" else (b, a)
        return _make(y, month, day)
    named = _DAY_MONTH.match(text)
    if named:
        return _make(int(named.group(3) or year), _month(named.group(2)), int(named.group(1)))
    named = _MONTH_DAY.match(text)
    if named:
        return _make(int(named.group(3) or year), _month(named.group(1)), int(named.group(2)))
    return None


def short(day: date) -> str:
    """`3 Nov` (the year only when it is not the current one is the caller's choice)."""
    return f"{day.day} {day.strftime('%b')}"


def _month(text: str) -> int:
    return next(i + 1 for i, m in enumerate(_MONTHS) if m.startswith(text[:3]))


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
