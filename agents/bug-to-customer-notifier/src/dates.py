"""Pure: dates parsed in code (ISO 8601 or epoch seconds, always UTC). Unreadable is missing, never guessed."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")


def parse_instant(value: str | None) -> datetime | None:
    """ISO 8601 (date or datetime, `Z` or offset; a naive time is UTC) or epoch seconds, in UTC."""
    text = (value or "").strip()
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_day(value: str | None) -> date | None:
    instant = parse_instant(value)
    return instant.date() if instant else None
