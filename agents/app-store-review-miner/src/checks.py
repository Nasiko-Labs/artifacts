"""Pure: the verbatim checks. A quote must be a substring of its own review (whitespace and case
normalised); a label or name may carry a number only when that number appears in the source text."""

from __future__ import annotations

import re

_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
MIN_LABEL_WORDS = 1
MAX_LABEL_WORDS = 8


def norm(text: str) -> str:
    return _SPACES.sub(" ", str(text or "")).strip().casefold()


def quote_ok(quote: str, source: str, max_chars: int) -> bool:
    q = norm(quote)
    return bool(q) and len(q) <= max_chars and q in norm(source)


def numbers_ok(text: str, source: str) -> bool:
    """Every number in `text` appears in `source`."""
    return all(n in source for n in _NUMBER.findall(text or ""))


def clean_label(label: str, source: str) -> str | None:
    words = _SPACES.sub(" ", str(label or "")).strip().strip(".").split(" ")
    if not words or not words[0] or not MIN_LABEL_WORDS <= len(words) <= MAX_LABEL_WORDS:
        return None
    text = " ".join(words)
    if not numbers_ok(text, source):
        return None
    return text[0].upper() + text[1:]
