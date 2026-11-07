"""Pure: what counts as being in the epic. Snippets must appear verbatim (after normalising whitespace, quotes
and case); numbers, months and acronyms in any story or criterion must appear in the epic or its constraints."""

from __future__ import annotations

import re

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
_SPACES = re.compile(r"\s+")
_EDGE = re.compile(r"^[\s\"'.,;:!?()\-]+|[\s\"'.,;:!?()\-]+$")
_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*%?")
_MONTH = re.compile(r"\b(?:january|february|march|april|june|july|august|september|october|november|december|"
                    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b", re.IGNORECASE)
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,9}s?\b|\b[A-Z][a-z]+[A-Z][A-Za-z]*\b")
_WORD = re.compile(r"[a-z0-9]+")
GENERIC_ACRONYMS = frozenset({"UI", "ID", "IDS", "URL", "OK", "GIVEN", "WHEN", "THEN", "AND", "I"})
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by", "with", "from", "as", "is",
    "are", "be", "can", "it", "its", "this", "that", "these", "those", "their", "them", "they", "i", "we",
    "you", "my", "our", "your", "so", "when", "then", "given", "which", "who", "what", "into", "than",
    "other", "another", "all", "any", "each", "via", "not", "no", "only", "out", "scope"
})
MIN_SNIPPET_CHARS = 3


def normalize(text: str) -> str:
    return _SPACES.sub(" ", (text or "").translate(_QUOTES)).strip().casefold()


def contains(ground_norm: str, snippet: str) -> bool:
    """True when `snippet` (normalised, edge punctuation trimmed) is a substring of the normalised ground."""
    needle = _EDGE.sub("", normalize(snippet))
    return len(needle) >= MIN_SNIPPET_CHARS and needle in ground_norm


def invented(text: str, ground: str) -> list[str]:
    """Numbers, months and acronyms/named systems in `text` that the ground never mentions."""
    ground_norm = normalize(ground)
    ground_numbers = set(_NUMBER.findall(ground_norm))
    found: list[str] = []
    for number in _NUMBER.findall(text or ""):
        if number not in ground_numbers and number.rstrip("%") not in ground_numbers:
            found.append(number)
    for month in _MONTH.findall(text or ""):
        if not re.search(rf"\b{re.escape(month.casefold())}\b", ground_norm):
            found.append(month)
    for name in _ACRONYM.findall(text or ""):
        base = name[:-1] if name.endswith("s") and name[:-1].isupper() else name
        if base.upper() not in GENERIC_ACRONYMS and base.casefold() not in ground_norm:
            found.append(name)
    return found


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def content_tokens(text: str) -> set[str]:
    return {stem(w) for w in _WORD.findall(normalize(text)) if w not in STOPWORDS}


def jaccard(a: str, b: str) -> float:
    left, right = content_tokens(a), content_tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def coverage(item: str, text: str) -> float:
    """Share of `item`'s content tokens that appear in `text`."""
    wanted = content_tokens(item)
    return len(wanted & content_tokens(text)) / len(wanted) if wanted else 0.0
