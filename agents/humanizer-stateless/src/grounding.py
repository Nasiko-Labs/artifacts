"""Pure: the facts of a text (numbers, money, percentages, dates, emails, links, capitalised names) and its
sentences, so code can check that a style pass added and removed none of them."""

from __future__ import annotations

import re
from collections import Counter

_NUMBER = re.compile(r"[$€£¥₹]?\d[\d,.:/]*(?:\s?(?:k|m|bn|b|%|percent)\b)?", re.IGNORECASE)
_LINK = re.compile(r"(?:https?://|www\.)[^\s<>()\"'`]+[^\s<>()\"'`.,;:!?]|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NAME = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:['’-][A-Za-z0-9]+)*\b")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
_BOUNDARY_FILL = " \t\"'“”‘’()*#>-•[]"
_NOT_NAMES = frozenset({"I", "I'm", "I'd", "I'll", "I've", "I’m", "I’d", "I’ll", "I’ve", "OK", "AI"})
_TRAILING = ".,;:!?"


def numbers(text: str) -> Counter:
    """Every number, amount, percentage and numeric date, with how often it occurs."""
    found = (m.group(0).rstrip(_TRAILING).casefold().replace(" ", "") for m in _NUMBER.finditer(text or ""))
    return Counter(n for n in found if any(c.isdigit() for c in n))


def links(text: str) -> Counter:
    return Counter(m.group(0).rstrip(_TRAILING).casefold() for m in _LINK.finditer(text or ""))


def capitalised(text: str) -> set[str]:
    """Every capitalised word, wherever it stands."""
    return {m.group(0) for m in _NAME.finditer(text or "")}


def names(text: str) -> set[str]:
    """Capitalised words that do not open a sentence, line, bullet or heading (likely names)."""
    found = set()
    for line in (text or "").splitlines():
        if line.lstrip().startswith("#"):
            continue  # heading words are styling, not names
        for m in _NAME.finditer(line):
            before = line[:m.start()].rstrip(_BOUNDARY_FILL)
            if before and before[-1] not in ".!?:" and m.group(0) not in _NOT_NAMES:
                found.add(m.group(0))
    return found


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_BREAK.split(text or "") if s.strip()]


def has_word(word: str, text: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(word)}(?![\w])", text or "") is not None
