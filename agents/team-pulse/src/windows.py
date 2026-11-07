"""Pure: the current window and the baseline, computed in code, plus time parsing and time-zone math.

W (current) is the 14 days ending yesterday 23:59:59 UTC. B (baseline) is the 28 days before W.
Nothing here guesses a time zone: local time is only computed for a zone the caller passes in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WINDOW_DAYS = 14
BASELINE_DAYS = 28
RECENT_DAYS = 7
WORK_START_HOUR = 9
WORK_END_HOUR = 18
WORK_DAYS = frozenset({0, 1, 2, 3, 4})  # Monday..Friday


@dataclass(frozen=True)
class Window:
    today: date

    @property
    def start(self) -> datetime:
        """Start of W (inclusive), UTC midnight."""
        return _midnight(self.today - timedelta(days=WINDOW_DAYS))

    @property
    def end(self) -> datetime:
        """End of W (exclusive): today 00:00 UTC, so yesterday 23:59:59 is the last second."""
        return _midnight(self.today)

    @property
    def baseline_start(self) -> datetime:
        return self.start - timedelta(days=BASELINE_DAYS)

    @property
    def recent_start(self) -> datetime:
        """Start of the last 7 days of W."""
        return self.end - timedelta(days=RECENT_DAYS)

    @property
    def window_weeks(self) -> float:
        return WINDOW_DAYS / 7

    @property
    def baseline_weeks(self) -> float:
        return BASELINE_DAYS / 7

    def in_range(self, moment: datetime) -> bool:
        """Inside [baseline start, window end)."""
        return self.baseline_start <= moment < self.end

    def in_window(self, moment: datetime) -> bool:
        return self.start <= moment < self.end

    def in_baseline(self, moment: datetime) -> bool:
        return self.baseline_start <= moment < self.start

    def in_recent(self, moment: datetime) -> bool:
        return self.recent_start <= moment < self.end


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


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
        parsed = datetime.fromisoformat(text)  # 3.11+: accepts a trailing "Z"
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _from_epoch(seconds: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def zone(name: object) -> ZoneInfo | None:
    """A valid IANA zone, or None. Never a default."""
    if not isinstance(name, str) or "/" not in name:
        return None
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def is_after_hours(moment: datetime, tz: ZoneInfo) -> bool:
    local = moment.astimezone(tz)
    if local.weekday() not in WORK_DAYS:
        return True
    return not (WORK_START_HOUR <= local.hour < WORK_END_HOUR)


def local_date(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


def hours_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


def days_between(start: datetime, today: date) -> int:
    return (today - start.astimezone(UTC).date()).days
