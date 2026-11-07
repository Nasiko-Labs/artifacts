"""Pure: the period of calls to mine, computed in code in every format a tool may want.

Default: the last 7 full days ending yesterday (UTC). "last week" is the previous Monday to Sunday.
"N days" / "two weeks" ask for up to 14 days, ending yesterday."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

DEFAULT_DAYS = 7
MAX_DAYS = 14
_LAST_WEEK = re.compile(r"\b(last|previous|past) (calendar )?week\b", re.IGNORECASE)
_DAYS = re.compile(r"\b(\d{1,3})\s*-?\s*days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|a)\s*weeks?\b", re.IGNORECASE)
_WORD = {"a": 1, "one": 1, "two": 2}
_MS_EPOCH = 10**11  # numbers above this are milliseconds


@dataclass(frozen=True)
class Window:
    start: date  # inclusive
    end: date    # inclusive

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def start_epoch(self) -> int:
        return int(datetime.combine(self.start, time.min, tzinfo=UTC).timestamp())

    @property
    def end_epoch(self) -> int:
        """Exclusive: midnight after the last day."""
        return int(datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=UTC).timestamp())

    @property
    def start_iso_z(self) -> str:
        return f"{self.start.isoformat()}T00:00:00Z"

    @property
    def end_iso_z(self) -> str:
        return f"{(self.end + timedelta(days=1)).isoformat()}T00:00:00Z"

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()} to {self.end.isoformat()}"

    def contains(self, instant: datetime) -> bool:
        return self.start_epoch <= instant.timestamp() < self.end_epoch

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}

    @staticmethod
    def from_dict(data: dict | None, today: date) -> Window:
        try:
            return Window(date.fromisoformat(data["start"]), date.fromisoformat(data["end"]))
        except (TypeError, KeyError, ValueError):
            return window_for("", today)


def window_for(text: str, today: date) -> Window:
    yesterday = today - timedelta(days=1)
    if _LAST_WEEK.search(text or "") and not _DAYS.search(text or ""):
        monday = today - timedelta(days=today.weekday())
        return Window(monday - timedelta(days=7), monday - timedelta(days=1))
    days = _requested_days(text or "")
    return Window(today - timedelta(days=days), yesterday)


def _requested_days(text: str) -> int:
    match = _DAYS.search(text)
    if match:
        return min(MAX_DAYS, max(1, int(match.group(1))))
    match = _WEEKS.search(text)
    if match:
        raw = match.group(1).casefold()
        weeks = _WORD.get(raw) or int(raw)
        return min(MAX_DAYS, max(1, weeks * 7))
    return DEFAULT_DAYS


def parse_instant(value: object) -> datetime | None:
    """An ISO 8601 time (Z or offset; naive = UTC) or a Unix time in seconds or milliseconds."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,14}(\.\d+)?", value.strip())):
        number = float(value)
        number = number / 1000 if number > _MS_EPOCH else number
        return datetime.fromtimestamp(number, tz=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_day(value: object) -> date | None:
    instant = parse_instant(value)
    if instant is not None:
        return instant.astimezone(UTC).date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None
