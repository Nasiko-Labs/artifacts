"""Pure: cell values typed in code: dates in mixed formats, viewer counts, refresh cadences. Anything that does
not parse is None, never a guess (an unparsable date is not "today")."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                           "dec"))}
_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T].*)?$")
_NUMERIC_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})(?:[ T].*)?$")
_DAY_MONTH = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[ -]([A-Za-z]{3,9})\.?,?[ -](\d{4})(?:[ T,].*)?$")
_MONTH_DAY = re.compile(r"^([A-Za-z]{3,9})\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})(?:[ T,].*)?$")
_EPOCH = re.compile(r"^\d{10}(?:\.\d+)?$")
_INT = re.compile(r"^-?\d{1,3}(?:,\d{3})+$|^-?\d+(?:\.0+)?$")
_EVERY_DAYS = re.compile(r"^(?:every\s+)?(\d{1,3})\s*(?:d|days?)$")
_EVERY_HOURS = re.compile(r"^(?:every\s+)?(\d{1,3})\s*(?:h|hrs?|hours?)$")
_CADENCE_WORDS = {
    "realtime": 1, "real-time": 1, "real time": 1, "live": 1, "hourly": 1, "daily": 1, "nightly": 1, "every day": 1,
    "weekdays": 1, "weekly": 7, "every week": 7, "biweekly": 14, "bi-weekly": 14, "fortnightly": 14,
    "monthly": 30, "every month": 30, "quarterly": 90, "yearly": 365, "annually": 365, "annual": 365,
}
_MIN_YEAR, _MAX_YEAR = 1990, 2100


def parse_date(raw: str) -> date | None:
    text = " ".join((raw or "").split())
    if not text:
        return None
    if _EPOCH.match(text):
        return datetime.fromtimestamp(float(text), UTC).date()
    for parser in (_iso, _numeric, _day_month, _month_day):
        found = parser(text)
        if found is not None:
            return found
    return None


def parse_count(raw: str) -> int | None:
    text = (raw or "").strip()
    if not _INT.match(text):
        return None
    value = int(float(text.replace(",", "")))
    return value if value >= 0 else None


def parse_cadence(raw: str) -> int | None:
    """Expected refresh interval in days (daily = 1, weekly = 7, monthly = 30), or None."""
    text = " ".join((raw or "").casefold().split())
    if text in _CADENCE_WORDS:
        return _CADENCE_WORDS[text]
    days = _EVERY_DAYS.match(text)
    if days and int(days.group(1)) > 0:
        return int(days.group(1))
    hours = _EVERY_HOURS.match(text)
    if hours and int(hours.group(1)) > 0:
        return max(1, int(hours.group(1)) // 24)
    return None


def _make(year: int, month: int, day: int) -> date | None:
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _iso(text: str) -> date | None:
    match = _ISO.match(text)
    return _make(int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def _numeric(text: str) -> date | None:
    """MM/DD/YYYY by default; DD/MM/YYYY when the first number cannot be a month."""
    match = _NUMERIC_DMY.match(text)
    if not match:
        return None
    first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    year = year + 2000 if year < 100 else year
    return _make(year, second, first) if first > 12 else _make(year, first, second)


def _month(word: str) -> int | None:
    return _MONTHS.get(word[:3].casefold())


def _day_month(text: str) -> date | None:
    match = _DAY_MONTH.match(text)
    month = _month(match.group(2)) if match else None
    return _make(int(match.group(3)), month, int(match.group(1))) if match and month else None


def _month_day(text: str) -> date | None:
    match = _MONTH_DAY.match(text)
    month = _month(match.group(1)) if match else None
    return _make(int(match.group(3)), month, int(match.group(2))) if match and month else None
