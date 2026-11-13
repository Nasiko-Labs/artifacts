"""Pure: dates and durations, computed in code from the timestamps written in the thread. The model
never computes a date or a duration; a timestamp that does not parse is left out of the span."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from messages import Message, own

_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%b %d %Y %H:%M", "%b %d %Y %I:%M %p", "%b %d %Y", "%B %d %Y %H:%M", "%B %d %Y %I:%M %p", "%B %d %Y",
    "%d %b %Y %H:%M", "%d %b %Y", "%d %B %Y %H:%M", "%d %B %Y",
    "%a %b %d %Y %H:%M", "%a %b %d %Y %I:%M %p", "%a %b %d %Y",
    "%A %B %d %Y %H:%M", "%A %B %d %Y %I:%M %p", "%A %B %d %Y",
    "%a %d %b %Y %H:%M:%S", "%a %d %b %Y %H:%M",
)
_NOISE = re.compile(r"\b(?:at|on)\b|,|\s(?:UTC|GMT|[A-Z]{2,4}T)\b|\s[+-]\d{4}\b|\(.*?\)")
_SPACES = re.compile(r"\s+")


def parse_when(text: str) -> datetime | None:
    """A timestamp as written in a header, or None (time-only stamps like "10:42" carry no date)."""
    cleaned = _SPACES.sub(" ", _NOISE.sub(" ", text or "")).strip()
    cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", cleaned)
    for fmt in _FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def span_days(messages: Sequence[Message]) -> int | None:
    """Calendar days from the first to the last dated message, both counted; None under two dates."""
    dates = [d for d in (parse_when(m.timestamp) for m in own(tuple(messages))) if d is not None]
    if len(dates) < 2:
        return None
    return (max(dates).date() - min(dates).date()).days + 1


def describe(messages: Sequence[Message]) -> str:
    """e.g. "thread of 41 messages over 6 days" (the header line and the envelope's `timeline`)."""
    count = len(own(tuple(messages)))
    unit = "message" if count == 1 else "messages"
    days = span_days(messages)
    if days is None:
        return f"thread of {count} {unit}"
    return f"thread of {count} {unit} " + ("on one day" if days == 1 else f"over {days} days")
