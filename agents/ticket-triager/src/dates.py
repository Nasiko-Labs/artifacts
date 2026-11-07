"""Pure: parsing pasted dates and computing ticket age against a clock (never in the model)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta

_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d",
            "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y")
_AS_OF = re.compile(r"\bas of (\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_FRACTION = re.compile(r"\.\d+")


def parse(value: str | None) -> datetime | None:
    """A UTC datetime, or None when the value is empty or not a format we read."""
    text = _FRACTION.sub("", (value or "").strip()).replace("Z", "+0000")
    if not text:
        return None
    text = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", text)
    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def as_of(text: str) -> datetime | None:
    """`as of YYYY-MM-DD` in the instruction: the end of that day (UTC), so same-day tickets have age >= 0."""
    match = _AS_OF.search(text or "")
    if not match:
        return None
    try:
        day = date.fromisoformat(match.group(1))
    except ValueError:
        return None
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


def age_hours(created: str | None, now: datetime) -> float | None:
    when = parse(created)
    if when is None:
        return None
    return round(max(timedelta(0), now - when).total_seconds() / 3600, 1)
