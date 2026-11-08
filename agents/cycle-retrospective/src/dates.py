"""Pure: reading tracker timestamps. Every comparison in the retro is made on these UTC instants."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def instant(text: str | None, end_of_day: bool = False) -> datetime | None:
    """An ISO timestamp as UTC; a bare date is that day's start (or, with `end_of_day`, its last second)."""
    raw = (text or "").strip()
    if not raw:
        return None
    if _DATE_ONLY.match(raw):
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            return None
        moment = datetime.combine(day, time.max if end_of_day else time.min, tzinfo=UTC)
        return moment.replace(microsecond=0)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def day(text: str | None) -> str:
    """`YYYY-MM-DD` of a timestamp, or "" when unreadable."""
    moment = instant(text)
    return moment.date().isoformat() if moment else ""


def hours(n: int) -> timedelta:
    return timedelta(hours=n)
