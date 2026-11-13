"""Pure: the time window a success measure states ("in month one", "in the first month", "within 30 days",
"by Q2"), so a metric's window comes from the brief instead of a [window?] placeholder."""

from __future__ import annotations

import re

_COUNT = r"(?:\d+|one|two|three|four|six|twelve)"
_UNIT = r"(?:days?|weeks?|months?|quarters?|years?)"
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_WINDOW = re.compile(
    rf"\b(in|during|over|within|by)\s+("
    rf"(?:the\s+)?(?:first|next)\s+(?:{_COUNT}\s+)?{_UNIT}"
    rf"|(?:month|week|quarter|year)\s+(?:one|two|three|\d)"
    rf"|{_COUNT}\s+{_UNIT}"
    rf"|q[1-4](?:\s+\d{{4}})?"
    rf"|(?:the\s+)?end\s+of\s+(?:q[1-4]|the\s+(?:month|quarter|year)|{_MONTH})"
    rf"|{_MONTH}(?:\s+\d{{4}})?"
    rf")\b", re.IGNORECASE)
_KEEP_PREPOSITION = ("within", "by")


def window_in(text: str) -> str | None:
    """The first window phrase in `text`; "within"/"by" stay part of it, "in"/"during"/"over" do not."""
    found = _WINDOW.search(text or "")
    if found is None:
        return None
    prep, phrase = found.group(1), " ".join(found.group(2).split())
    return f"{prep.casefold()} {phrase}" if prep.casefold() in _KEEP_PREPOSITION else phrase


def strip_windows(text: str) -> str:
    """`text` without its window phrases, so "within 30 days" is not read as a baseline or target."""
    return _WINDOW.sub(" ", text or "")
