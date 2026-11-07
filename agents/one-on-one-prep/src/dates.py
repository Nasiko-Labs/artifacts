"""Pure: dates as written in notes ("Oct 28", "2026-10-28", "Friday") resolved against the last 1:1 and the
clock. All date maths is here; the model never computes a date or a day count."""

from __future__ import annotations

import re
from datetime import date, timedelta

FUTURE_SLACK_DAYS = 180
_MONTHS = {m: i for i, names in enumerate(
    (("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")), start=1) for m in names}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
                        re.IGNORECASE)
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b",
                        re.IGNORECASE)
_WEEKDAY = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _with_year(month: int, day: int, today: date) -> date | None:
    found = _safe(today.year, month, day)
    if found and found > today + timedelta(days=FUTURE_SLACK_DAYS):
        found = _safe(today.year - 1, month, day)
    return found


def parse(text: str, today: date, anchor: date | None = None) -> date | None:
    """The first date in `text`; a weekday is the first such day after `anchor` (the last 1:1)."""
    value = text or ""
    iso = _ISO.search(value)
    if iso:
        return _safe(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    md = _MONTH_DAY.search(value)
    if md:
        return _with_year(_MONTHS[md.group(1).casefold()], int(md.group(2)), today)
    dm = _DAY_MONTH.search(value)
    if dm:
        return _with_year(_MONTHS[dm.group(2).casefold()], int(dm.group(1)), today)
    weekday = _WEEKDAY.search(value)
    if weekday and anchor:
        target = _WEEKDAYS.index(weekday.group(1).casefold())
        ahead = (target - anchor.weekday()) % 7 or 7
        return anchor + timedelta(days=ahead)
    return None


def short(day: date) -> str:
    return f"{day.strftime('%b')} {day.day}"


def overdue_days(due: date | None, today: date) -> int:
    """Days past due (0 when not past due or unknown)."""
    return max(0, (today - due).days) if due else 0
