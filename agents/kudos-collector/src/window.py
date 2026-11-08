"""Pure: the review window, computed in code from the user's words and a fixed `today`.

Default 90 days (a review cycle) ending today. Phrases: "this half", "last half", "this quarter",
"last quarter", "since <month>", "last N days|weeks|months", "this year". Every window is clamped to
14..180 days; a clamp is reported. The end is exclusive: tomorrow 00:00 UTC, so today is included.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

DEFAULT_DAYS = 90
MIN_DAYS = 14
MAX_DAYS = 180
DAYS_PER_MONTH = 30
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_MONTH_RE = "|".join([*_MONTHS, *(m[:3] for m in _MONTHS), "sept"])
_SINCE = re.compile(rf"\bsince\s+({_MONTH_RE})\b\.?(?:\s+(\d{{4}}))?", re.IGNORECASE)
_LAST_N = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(day|week|month)s?\b", re.IGNORECASE)
_N_MONTHS = re.compile(r"\b(\d{1,2})\s+months?\b", re.IGNORECASE)
_THIS_HALF = re.compile(r"\b(?:this|current)\s+half\b|\bh[12] to date\b", re.IGNORECASE)
_LAST_HALF = re.compile(r"\b(?:last|previous|prior)\s+half\b", re.IGNORECASE)
_THIS_Q = re.compile(r"\b(?:this|current)\s+quarter\b", re.IGNORECASE)
_LAST_Q = re.compile(r"\b(?:last|previous|prior)\s+quarter\b", re.IGNORECASE)
_THIS_YEAR = re.compile(r"\bthis\s+year\b|\bytd\b|\byear to date\b", re.IGNORECASE)
_UNIT_DAYS = {"day": 1, "week": 7, "month": DAYS_PER_MONTH}


@dataclass(frozen=True)
class Window:
    start: date  # inclusive
    end: date  # exclusive
    phrase: str = ""  # the phrase that set it, "" for the default
    clamped: bool = False

    @property
    def start_at(self) -> datetime:
        return _midnight(self.start)

    @property
    def end_at(self) -> datetime:
        return _midnight(self.end)

    @property
    def last_day(self) -> date:
        return self.end - timedelta(days=1)

    @property
    def days(self) -> int:
        return (self.end - self.start).days

    def contains(self, moment: datetime) -> bool:
        return self.start_at <= moment < self.end_at

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.last_day.isoformat(), "days": self.days,
                "clamped": self.clamped}


def parse_window(text: str, today: date) -> Window:
    end = today + timedelta(days=1)
    start, finish, phrase = _requested(text or "", today, end)
    return _clamp(start, finish, phrase)


def explicit(text: str) -> bool:
    """True when the message sets a window (a follow-up then needs a new pull)."""
    probe = parse_window(text, date(2000, 6, 15))
    return bool(probe.phrase)


def _requested(text: str, today: date, end: date) -> tuple[date, date, str]:
    if m := _SINCE.search(text):
        month = _month(m.group(1))
        year = int(m.group(2)) if m.group(2) else today.year
        start = date(year, month, 1)
        if start > today and not m.group(2):
            start = date(year - 1, month, 1)
        return start, end, m.group(0)
    if m := _LAST_N.search(text):
        return end - timedelta(days=int(m.group(1)) * _UNIT_DAYS[m.group(2).lower()]), end, m.group(0)
    half_start = date(today.year, 1 if today.month <= 6 else 7, 1)
    q_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
    if m := _LAST_HALF.search(text):
        prev = date(half_start.year - 1, 7, 1) if half_start.month == 1 else date(half_start.year, 1, 1)
        return prev, half_start, m.group(0)
    if m := _THIS_HALF.search(text):
        return half_start, end, m.group(0)
    if m := _LAST_Q.search(text):
        prev = date(q_start.year - 1, 10, 1) if q_start.month == 1 else date(q_start.year, q_start.month - 3, 1)
        return prev, q_start, m.group(0)
    if m := _THIS_Q.search(text):
        return q_start, end, m.group(0)
    if m := _THIS_YEAR.search(text):
        return date(today.year, 1, 1), end, m.group(0)
    if m := _N_MONTHS.search(text):
        return end - timedelta(days=int(m.group(1)) * DAYS_PER_MONTH), end, m.group(0)
    return end - timedelta(days=DEFAULT_DAYS), end, ""


def _clamp(start: date, end: date, phrase: str) -> Window:
    days = (end - start).days
    if days > MAX_DAYS:
        return Window(end - timedelta(days=MAX_DAYS), end, phrase, clamped=True)
    if days < MIN_DAYS:
        return Window(end - timedelta(days=MIN_DAYS), end, phrase, clamped=True)
    return Window(start, end, phrase)


def _month(name: str) -> int:
    key = name.casefold()[:3]
    return next(i for i, m in enumerate(_MONTHS, start=1) if m.startswith(key))


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def parse_time(raw: object) -> datetime | None:
    """An ISO 8601 string (naive = UTC) or Unix seconds (also as text, e.g. a chat `ts`), as aware UTC."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return _from_epoch(float(raw))
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        return _from_epoch(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _from_epoch(seconds: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
