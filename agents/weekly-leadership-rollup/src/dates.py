"""Pure: the rollup window and every date the agent shows or compares, computed in code.

The window is the last N days ending today (UTC calendar days, both ends inclusive). "Next week" is
today through today + 7. Nothing here reads a clock: the executor passes today in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

DEFAULT_DAYS = 7
MIN_DAYS = 3
MAX_DAYS = 14
NEXT_WEEK_DAYS = 7
IN_FLIGHT_LOOKBACK_DAYS = 90

_EPOCH = re.compile(r"^\d{9,11}(?:\.\d+)?$")


@dataclass(frozen=True)
class Window:
    today: date
    days: int = DEFAULT_DAYS

    @property
    def start(self) -> date:
        return self.today - timedelta(days=self.days)

    @property
    def next_week_end(self) -> date:
        return self.today + timedelta(days=NEXT_WEEK_DAYS)

    @property
    def in_flight_start(self) -> date:
        return self.today - timedelta(days=IN_FLIGHT_LOOKBACK_DAYS)

    def in_window(self, day: date | None) -> bool:
        return day is not None and self.start <= day <= self.today

    def in_next_week(self, day: date | None) -> bool:
        return day is not None and self.today <= day <= self.next_week_end


def parse_time(value: object) -> datetime | None:
    """ISO 8601 (date or date-time, `Z` allowed) or epoch seconds (chat timestamps) as aware UTC."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return _epoch(float(value))
    text = str(value).strip()
    if not text:
        return None
    if _EPOCH.match(text):
        return _epoch(float(text))
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_day(value: object) -> date | None:
    moment = parse_time(value)
    return moment.date() if moment else None


def short(day: date) -> str:
    """`Nov 5`: how dates are shown in the rollup."""
    return f"{day.strftime('%b')} {day.day}"


def plural(n: int, word: str = "day") -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _epoch(seconds: float) -> datetime | None:
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
