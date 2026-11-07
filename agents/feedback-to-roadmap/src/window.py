"""Pure: the time window of a pull, parsed in code from the user's words against the turn's fixed clock.

"last 2 weeks", "past 45 days", "this month", "this quarter", "last month", "since 2026-10-01", an ISO range
"2026-10-01 to 2026-10-31", "since last run". No window -> the default (30 days). Always clamped to 7-90
days; the clamp is reported."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_UNITS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30}
_LAST_N = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(days?|weeks?|months?)\b", re.IGNORECASE)
_LAST_ONE = re.compile(r"\b(?:last|past|previous)\s+(day|week|month)\b", re.IGNORECASE)
_RANGE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*(?:to|until|through|-|–|\.\.)\s*(\d{4}-\d{2}-\d{2})")
_SINCE = re.compile(r"\b(?:since|from|after)\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)
_THIS = re.compile(r"\bthis\s+(week|month|quarter)\b", re.IGNORECASE)
_LAST_QUARTER = re.compile(r"\blast\s+quarter\b", re.IGNORECASE)
_SINCE_LAST_RUN = re.compile(r"\bsince\s+(?:the\s+|my\s+)?last\s+(?:run|pull|time)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Window:
    start: date
    end: date
    clamped: bool = False
    asked: bool = False     # the user named a window

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def label(self) -> str:
        return f"{self.start.isoformat()} to {self.end.isoformat()}"


def parse(text: str, today: date, default_days: int = 30, low: int = 7, high: int = 90,
          last_run: date | None = None) -> Window:
    start, end = _start_end(text or "", today, last_run)
    asked = start is not None
    if start is None:
        start, end = today - timedelta(days=default_days), today
    end = min(end, today)
    days = (end - start).days
    if days < low or days > high:
        start = end - timedelta(days=max(low, min(high, days)))
        return Window(start, end, clamped=True, asked=asked)
    return Window(start, end, asked=asked)


def _start_end(text: str, today: date, last_run: date | None) -> tuple[date | None, date]:
    if last_run is not None and _SINCE_LAST_RUN.search(text):
        return last_run, today
    if match := _RANGE.search(text):
        first, second = _day(match.group(1)), _day(match.group(2))
        if first and second:
            return (min(first, second), max(first, second))
    if (match := _SINCE.search(text)) and (day := _day(match.group(1))):
        return day, today
    if match := _LAST_N.search(text):
        return today - timedelta(days=int(match.group(1)) * _UNITS[match.group(2).casefold()]), today
    if match := _LAST_ONE.search(text):
        return today - timedelta(days=_UNITS[match.group(1).casefold()]), today
    if _LAST_QUARTER.search(text):
        first = _quarter_start(today)
        prev = _quarter_start(first - timedelta(days=1))
        return prev, first
    if match := _THIS.search(text):
        unit = match.group(1).casefold()
        if unit == "week":
            return today - timedelta(days=today.weekday()), today
        if unit == "month":
            return today.replace(day=1), today
        return _quarter_start(today), today
    return None, today


def _quarter_start(day: date) -> date:
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None
