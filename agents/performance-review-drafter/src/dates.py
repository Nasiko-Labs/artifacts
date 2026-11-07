"""Pure: dates as written in the evidence and the review period. No date maths happens in the model."""

from __future__ import annotations

import calendar
import re
from datetime import date

MONTHS = {name.casefold(): n for n, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.casefold(): n for n, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|" \
       r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO = re.compile(r"\b(20\d{2})[-/](\d{1,2})(?:[-/](\d{1,2}))?\b")
_MDY = re.compile(rf"\b{_MON}\.? (\d{{1,2}})(?:st|nd|rd|th)?(?:,? (20\d{{2}}))?\b", re.IGNORECASE)
_DMY = re.compile(rf"\b(\d{{1,2}}) {_MON}\.?(?:,? (20\d{{2}}))?\b", re.IGNORECASE)
_RANGE_SEP = r"\s*(?:to|until|through|-|–|—)\s*"
_ISO_RANGE = re.compile(rf"(20\d{{2}}-\d{{1,2}}(?:-\d{{1,2}})?){_RANGE_SEP}(20\d{{2}}-\d{{1,2}}(?:-\d{{1,2}})?)")
_MONTH_RANGE = re.compile(rf"{_MON}\.?(?: (20\d{{2}}))?{_RANGE_SEP}{_MON}\.?,? (20\d{{2}})", re.IGNORECASE)
_QUARTER = re.compile(r"\bq([1-4])\s*(20\d{2})\b", re.IGNORECASE)
_HALF = re.compile(r"\bh([12])\s*(20\d{2})\b", re.IGNORECASE)


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month(name: str) -> int:
    return MONTHS.get(name.casefold().rstrip("."), MONTHS.get(name.casefold()[:3], 0))


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def find_date(text: str, default_year: int) -> date | None:
    """The first date written in `text` (ISO, "May 12, 2026", "12 May"); a bare year-month is its 1st."""
    candidates: list[tuple[int, date]] = []
    for m in _ISO.finditer(text):
        d = _safe(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
        if d:
            candidates.append((m.start(), d))
    for m in _MDY.finditer(text):
        d = _safe(int(m.group(3) or default_year), _month(m.group(1)), int(m.group(2)))
        if d:
            candidates.append((m.start(), d))
    for m in _DMY.finditer(text):
        d = _safe(int(m.group(3) or default_year), _month(m.group(2)), int(m.group(1)))
        if d:
            candidates.append((m.start(), d))
    return min(candidates, key=lambda c: c[0])[1] if candidates else None


def _iso_bound(text: str, end: bool) -> date | None:
    parts = [int(p) for p in text.split("-")]
    if len(parts) == 3:
        return _safe(*parts)
    return month_end(parts[0], parts[1]) if end else _safe(parts[0], parts[1], 1)


def parse_period(text: str) -> tuple[date, date] | None:
    """`Period:` value -> (start, end): ISO pair, "Jan to Jun 2026", "Q2 2026", "H1 2026"."""
    value = (text or "").strip()
    if m := _ISO_RANGE.search(value):
        start, end = _iso_bound(m.group(1), False), _iso_bound(m.group(2), True)
        return (start, end) if start and end and start <= end else None
    if m := _MONTH_RANGE.search(value):
        end_year = int(m.group(4))
        start_year = int(m.group(2) or end_year)
        start, end = _safe(start_year, _month(m.group(1)), 1), month_end(end_year, _month(m.group(3)))
        return (start, end) if start and start <= end else None
    if m := _QUARTER.search(value):
        q, year = int(m.group(1)), int(m.group(2))
        return date(year, 3 * q - 2, 1), month_end(year, 3 * q)
    if m := _HALF.search(value):
        h, year = int(m.group(1)), int(m.group(2))
        return date(year, 6 * h - 5, 1), month_end(year, 6 * h)
    return None
