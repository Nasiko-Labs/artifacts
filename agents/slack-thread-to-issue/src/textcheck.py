"""Pure: the verbatim checks. A quote, id or link the model returns counts only when it occurs in this turn's
tool output or pasted input, compared after normalising case, whitespace, curly quotes and JSON escapes."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

_ISO_DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})")
_EPOCH_SECONDS = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_EPOCH_MILLIS = re.compile(r"^\s*(\d{12,14})\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_RFC_2822 = ("%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z", "%a, %d %b %Y", "%d %b %Y", "%b %d, %Y",
             "%B %d, %Y", "%d %B %Y")


def normalise(text: str) -> str:
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str, haystack: str) -> bool:
    """`needle` occurs in `haystack` (both normalised). Empty never matches."""
    wanted = normalise(needle).strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in normalise(haystack)


def parse_day(value: str | None) -> date | None:
    """An ISO date/datetime, epoch seconds or milliseconds, or an RFC 2822 date, as a UTC date."""
    text = (value or "").strip()
    for pattern, divisor in ((_EPOCH_SECONDS, 1), (_EPOCH_MILLIS, 1000)):
        match = pattern.match(text)
        if match:
            return datetime.fromtimestamp(int(match.group(1)) // divisor, tz=UTC).date()
    match = _ISO_DATE.match(text)
    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            return None
    for fmt in _RFC_2822:
        try:
            return datetime.strptime(text.split(" (")[0].strip(), fmt).date()
        except ValueError:
            continue
    return None
