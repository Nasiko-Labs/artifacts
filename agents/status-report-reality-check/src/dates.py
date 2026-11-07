"""Pure: dates in export cells, and every age / overdue figure (the model never does date maths).

Slashed numeric dates (`01/02/2026`) are ambiguous: day-first or month-first. The order is decided once
per export from its own cells: a cell whose first number is over 12 proves day-first, a second number
over 12 proves month-first. With no proof (or contradicting proof) slashed dates stay unparsed, with a
caveat, rather than guessed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

DMY, MDY = "dmy", "mdy"

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:$|[T\s])")
_SLASH = re.compile(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4}|\d{2})(?:$|\s)")
_DAY_MON = re.compile(r"^(\d{1,2})[\s/-]([A-Za-z]{3,9})\.?[\s/-],?\s*(\d{4}|\d{2})(?:$|\s)")
_MON_DAY = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?:$|\s)")
_MONTHS = {name: n for n, names in enumerate(
    (("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")), start=1) for name in names}


def slash_order(values: Iterable[str]) -> str | None:
    """DMY or MDY when the export's own slashed cells prove one order; None when nothing (or both) does."""
    day_first = month_first = False
    for value in values:
        match = _SLASH.match((value or "").strip())
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        day_first |= first > 12 >= second
        month_first |= second > 12 >= first
    if day_first == month_first:
        return None
    return DMY if day_first else MDY


def is_slashed(value: str) -> bool:
    return bool(_SLASH.match((value or "").strip()))


def parse(value: str, order: str | None = None) -> date | None:
    """A cell as a date, or None when it is empty or can't be read without guessing."""
    text = (value or "").strip()
    if not text:
        return None
    for reader in (_iso, _day_month_name, _month_name_day):
        found = reader(text)
        if found is not None:
            return found
    return _slashed(text, order)


def iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def from_iso(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def days_between(earlier: date, later: date) -> int:
    return (later - earlier).days


def plural(n: int, word: str = "day") -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _iso(text: str) -> date | None:
    match = _ISO.match(text)
    return _safe(int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def _day_month_name(text: str) -> date | None:
    match = _DAY_MON.match(text)
    if not match or match.group(2).casefold() not in _MONTHS:
        return None
    return _safe(_year(match.group(3)), _MONTHS[match.group(2).casefold()], int(match.group(1)))


def _month_name_day(text: str) -> date | None:
    match = _MON_DAY.match(text)
    if not match or match.group(1).casefold() not in _MONTHS:
        return None
    return _safe(int(match.group(3)), _MONTHS[match.group(1).casefold()], int(match.group(2)))


def _slashed(text: str, order: str | None) -> date | None:
    match = _SLASH.match(text)
    if not match:
        return None
    first, second, year = int(match.group(1)), int(match.group(2)), _year(match.group(3))
    if first == second:  # 03/03/2026 reads the same either way
        return _safe(year, first, second)
    if order == DMY:
        return _safe(year, second, first)
    if order == MDY:
        return _safe(year, first, second)
    return None


def _year(text: str) -> int:
    value = int(text)
    return value + 2000 if value < 100 else value


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
