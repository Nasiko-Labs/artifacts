"""Pure: the standup window, computed in code from the turn clock (UTC) and the user's words.

Default "since the last working day": 24 h, or 72 h when the turn clock falls on a Monday. The user
may say "last 3 days", "past 2 days", "since Friday", "this week", "today" or "yesterday". A window is
never longer than MAX_DAYS. A record exactly at `since` is inside the window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

DEFAULT_HOURS = 24
MONDAY_HOURS = 72
MAX_DAYS = 14
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_LAST_N = re.compile(r"\b(?:last|past)\s+(\d{1,3})\s+(day|days|hour|hours|week|weeks)\b", re.IGNORECASE)
_SINCE_DAY = re.compile(r"\bsince\s+(?:last\s+)?(" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE)
_THIS_WEEK = re.compile(r"\bthis\s+week\b", re.IGNORECASE)
_LAST_WEEK = re.compile(r"\b(?:last|past)\s+week\b", re.IGNORECASE)
_TODAY = re.compile(r"\btoday\b", re.IGNORECASE)
_YESTERDAY = re.compile(r"\b(?:since\s+)?yesterday\b", re.IGNORECASE)


@dataclass(frozen=True)
class Window:
    since: datetime
    end: datetime

    @property
    def hours(self) -> int:
        return round((self.end - self.since).total_seconds() / 3600)

    def contains(self, moment: datetime | None) -> bool:
        return moment is not None and self.since <= moment <= self.end

    def describe(self) -> str:
        return f"since {_stamp(self.since)} ({self.hours} h, window ends {_stamp(self.end)})"

    def to_dict(self) -> dict:
        return {"since": self.since.isoformat(), "end": self.end.isoformat()}

    @staticmethod
    def from_dict(raw: dict) -> Window:
        return Window(datetime.fromisoformat(raw["since"]), datetime.fromisoformat(raw["end"]))


def default_window(now: datetime) -> Window:
    hours = MONDAY_HOURS if now.weekday() == 0 else DEFAULT_HOURS
    return Window(now - timedelta(hours=hours), now)


def parse_window(text: str, now: datetime) -> Window | None:
    """The window the user's words ask for, or None when they name none."""
    text = text or ""
    found = _LAST_N.search(text)
    if found:
        count, unit = int(found.group(1)), found.group(2).lower()
        delta = (timedelta(hours=count) if unit.startswith("hour")
                 else timedelta(weeks=count) if unit.startswith("week") else timedelta(days=count))
        return _clamped(now - max(delta, timedelta(hours=1)), now)
    found = _SINCE_DAY.search(text)
    if found:
        wanted = WEEKDAYS.index(found.group(1).lower())
        back = (now.weekday() - wanted) % 7 or 7
        return _clamped(_midnight(now - timedelta(days=back)), now)
    if _THIS_WEEK.search(text):
        return _clamped(_midnight(now - timedelta(days=now.weekday())), now)
    if _LAST_WEEK.search(text):
        return _clamped(now - timedelta(days=7), now)
    if _TODAY.search(text):
        return _clamped(_midnight(now), now)
    if _YESTERDAY.search(text):
        return _clamped(_midnight(now - timedelta(days=1)), now)
    return None


def window_for(text: str, now: datetime) -> Window:
    return parse_window(text, now) or default_window(now)


def _clamped(since: datetime, now: datetime) -> Window:
    return Window(max(since, now - timedelta(days=MAX_DAYS)), now)


def _midnight(moment: datetime) -> datetime:
    return datetime.combine(moment.date(), time.min, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    utc = moment.astimezone(UTC)
    return f"{utc.strftime('%a')} {utc.day} {utc.strftime('%b %H:%M')} UTC"


def parse_time(raw: object) -> datetime | None:
    """An ISO 8601 string (with or without offset; naive = UTC) or Unix seconds, as an aware UTC datetime."""
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
