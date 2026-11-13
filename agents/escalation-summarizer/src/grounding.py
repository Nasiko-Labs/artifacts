"""Pure: is a quote, a name or a number really in the source? Both sides are normalised the same way."""

from __future__ import annotations

import re
import unicodedata

MIN_SNIPPET_CHARS = 12
ELLIPSIS_SPAN_CHARS = 600
OWNER_WINDOW_CHARS = 400
DUE_WINDOW_CHARS = 400

_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                             "…": "...", " ": " "})
# Transcript timestamps only: cue timings, bracketed times, and a time with seconds that opens a line.
# A time inside a sentence ("at 10:30") is content and stays.
_TIME = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
_TIMESTAMP = re.compile(rf"{_TIME}\s*-->\s*{_TIME}|[\[(]{_TIME}[\])]|^[ \t]*\d{{1,2}}:\d{{2}}:\d{{2}}(?:[.,]\d+)?",
                        re.MULTILINE)
_WHITESPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.{3,}|\[\s*\.{3}\s*\]")
_EDGE = " .,;:!?\"'()[]-"

_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = "january|february|march|april|june|july|august|september|october|november|december"
_TOKEN = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                       # email
    r"|https?://\S+|www\.\S+"                             # url
    r"|[$€£¥₹]?\d[\d,.]*(?:\s?(?:k|m|bn|b|%|percent))?"   # number, money, percentage
    rf"|\b(?:{_WEEKDAYS}|{_MONTHS})\b"                    # weekday, month
    r"|\bmay(?=\s+\d)|(?<=\d\s)may\b",                    # "may" only as a month next to a day
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}\"'"
_CALENDAR_WORD = re.compile(rf"(?:{_WEEKDAYS}|{_MONTHS})")


def normalise(text: str) -> str:
    """Casefold, NFKC, straight quotes, plain dashes, no timestamps, single spaces."""
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", _TIMESTAMP.sub(" ", plain)).strip().casefold()


def locate(snippet: str, norm_source: str) -> tuple[int, int] | None:
    """(start, end) of the snippet in the normalised source, or None. An ellipsis splits it: each piece
    must be found, in order, all within ELLIPSIS_SPAN_CHARS. Too short a snippet never matches."""
    pieces = [p.strip(_EDGE) for p in _ELLIPSIS.split(normalise(snippet))]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < MIN_SNIPPET_CHARS:
        return None
    first = pieces[0]
    at = norm_source.find(first)
    while at != -1:
        end = _rest_in_order(pieces[1:], norm_source, at + len(first), at + ELLIPSIS_SPAN_CHARS)
        if end is not None:
            return at, end
        at = norm_source.find(first, at + 1)
    return None


def _rest_in_order(pieces: list[str], norm_source: str, cursor: int, limit: int) -> int | None:
    for piece in pieces:
        found = norm_source.find(piece, cursor, limit)
        if found == -1:
            return None
        cursor = found + len(piece)
    return cursor


def near(value: str, norm_source: str, span: tuple[int, int], before: int, after: int) -> bool:
    """`value` occurs in the source between `before` chars ahead of the span and `after` chars past it."""
    wanted = normalise(value).strip(_EDGE)
    if not wanted:
        return False
    start, end = span
    return wanted in norm_source[max(0, start - before):end + after]


def tokens(text: str) -> tuple[str, ...]:
    """Every number, amount, percentage, date word, email and URL in `text`."""
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(text or ""))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def ungrounded_tokens(text: str, norm_source: str) -> tuple[str, ...]:
    """The tokens of `text` that do not appear in the source. A month or weekday written in full stands
    when the source abbreviates it ("November" for "Nov", "Friday" for "Fri")."""
    return tuple(t for t in tokens(text) if normalise(t) not in norm_source and not _abbreviated(t, norm_source))


def is_calendar_word(word: str) -> bool:
    return bool(_CALENDAR_WORD.fullmatch(word.casefold()))


def _abbreviated(token: str, norm_source: str) -> bool:
    word = token.casefold()
    if not is_calendar_word(word):
        return False
    return any(re.search(rf"\b{a}\.?(?![a-z])", norm_source) for a in {word[:3], word[:4]})


CONTENT_WORD_CHARS = 4
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""this that with have has from their there they were been will what when which into about after
before over under than then them these those also just only very more most some such each other being does ours
your""".split())


def content_words(text: str) -> set[str]:
    """The words that carry meaning: 4+ letters or a number, casefolded, no possessive 's, no stop words."""
    plain = re.sub(r"['’]s\b", "", normalise(text))
    return {w for w in _WORD.findall(plain) if (len(w) >= CONTENT_WORD_CHARS or w.isdigit()) and w not in _STOP}


def support(text: str, norm_source: str) -> float:
    """The share of `text`'s content words that occur in the (normalised) source; 1.0 when it has none."""
    words = content_words(text)
    found = set(_WORD.findall(norm_source))
    return len(words & found) / len(words) if words else 1.0

