"""Pure: date phrases in the user's words resolved in code to inclusive date ranges, relative to the turn's
clock. The model never does date maths: it is handed these exact ranges and only places them in the spec.

Weeks run Monday to Sunday. "last N days/weeks" ends today; "this week" is the whole week containing
today; a month name means the most recent such month that has started (this year, else last year).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_MONTH_RE = "|".join(f"{m[:3]}(?:{m[3:]})?" for m in _MONTHS)
_UNIT_DAYS = {"day": 1, "week": 7}
_ORDINAL = r"(\d{1,2})(?:st|nd|rd|th)?"
_LAST_N = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(day|week|month)s?\b")
_MONTH_NAME = re.compile(rf"\b({_MONTH_RE})\b(?:\s+(\d{{4}}))?")
_SINCE_ORDINAL = re.compile(rf"\bsince\s+the\s+{_ORDINAL}\b")
_SINCE_DAY_MONTH = re.compile(rf"\bsince\s+{_ORDINAL}\s+({_MONTH_RE})\b")
_SINCE_MONTH_DAY = re.compile(rf"\bsince\s+({_MONTH_RE})\s+{_ORDINAL}\b")
_ISO_RANGE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\s*(?:to|until|through|-|–)\s*(\d{4}-\d{2}-\d{2})\b")


@dataclass(frozen=True)
class DateRange:
    phrase: str
    start: date
    end: date

    def to_dict(self) -> dict:
        return {"phrase": self.phrase, "start": self.start.isoformat(), "end": self.end.isoformat()}


def resolve(text: str, today: date) -> tuple[DateRange, ...]:
    """Every date phrase found in `text`, first occurrence wins per phrase."""
    lowered = (text or "").casefold()
    found: list[DateRange] = []
    for finder in (_iso_ranges, _last_n, _fixed_phrases, _since, _month_names):
        found.extend(finder(lowered, today))
    unique: dict[str, DateRange] = {}
    for item in found:
        unique.setdefault(item.phrase, item)
    return tuple(unique.values())


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def month_range(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return first, nxt - timedelta(days=1)


def _iso_ranges(text: str, today: date) -> list[DateRange]:
    out = []
    for m in _ISO_RANGE.finditer(text):
        try:
            a, b = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        except ValueError:
            continue
        out.append(DateRange(m.group(0), min(a, b), max(a, b)))
    return out


def _last_n(text: str, today: date) -> list[DateRange]:
    out = []
    for m in _LAST_N.finditer(text):
        n, unit = int(m.group(1)), m.group(2)
        if n < 1:
            continue
        if unit == "month":
            start = _months_back(today, n) + timedelta(days=1)
        else:
            start = today - timedelta(days=n * _UNIT_DAYS[unit] - 1)
        out.append(DateRange(m.group(0), start, today))
    return out


def _fixed_phrases(text: str, today: date) -> list[DateRange]:
    this_week = week_start(today)
    last_month_end = date(today.year, today.month, 1) - timedelta(days=1)
    phrases = {
        "today": (today, today),
        "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
        "this week": (this_week, this_week + timedelta(days=6)),
        "last week": (this_week - timedelta(days=7), this_week - timedelta(days=1)),
        "this month": month_range(today.year, today.month),
        "last month": month_range(last_month_end.year, last_month_end.month),
        "this year": (date(today.year, 1, 1), date(today.year, 12, 31)),
        "last year": (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
    }
    return [DateRange(p, *rng) for p, rng in phrases.items() if re.search(rf"\b{p}\b", text)]


def _since(text: str, today: date) -> list[DateRange]:
    out = []
    for m in _SINCE_ORDINAL.finditer(text):
        day = _safe_date(today.year, today.month, int(m.group(1)))
        if day is not None and day > today:
            prev = date(today.year, today.month, 1) - timedelta(days=1)
            day = _safe_date(prev.year, prev.month, int(m.group(1)))
        if day is not None:
            out.append(DateRange(m.group(0), day, today))
    for m in (*_SINCE_DAY_MONTH.finditer(text), *_SINCE_MONTH_DAY.finditer(text)):
        groups = m.groups()
        day_text, month_text = (groups[0], groups[1]) if groups[0].isdigit() else (groups[1], groups[0])
        month = _month_number(month_text)
        year = today.year if month <= today.month else today.year - 1
        day = _safe_date(year, month, int(day_text))
        if day is not None:
            out.append(DateRange(m.group(0), day, today))
    return out


def _month_names(text: str, today: date) -> list[DateRange]:
    out = []
    for m in _MONTH_NAME.finditer(text):
        if m.group(1) == "may" and not m.group(2):
            continue  # "may" is too often the verb; "May 2026" still counts
        month = _month_number(m.group(1))
        if _inside_since(text, m.start()):
            continue
        year = int(m.group(2)) if m.group(2) else (today.year if month <= today.month else today.year - 1)
        out.append(DateRange(m.group(0), *month_range(year, month)))
    return out


def _inside_since(text: str, pos: int) -> bool:
    return bool(re.search(r"since\s+(?:\d{1,2}(?:st|nd|rd|th)?\s+)?$", text[:pos]))


def _month_number(text: str) -> int:
    return next(i + 1 for i, m in enumerate(_MONTHS) if m.startswith(text[:3]))


def _months_back(day: date, n: int) -> date:
    month_index = day.year * 12 + (day.month - 1) - n
    year, month = divmod(month_index, 12)
    last = month_range(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last.day))


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
