"""Pure: is a quote, a name or a number really in the transcript? Both sides are normalised the same way:
casefold, NFKC, straight quotes, plain dashes, single spaces, and spoken fillers ("um", "uh", "you know")
removed - for matching only; the quote shown is the model's, once it is found."""

from __future__ import annotations

import re
import unicodedata

MIN_QUOTE_CHARS = 12
ELLIPSIS_SPAN_CHARS = 600

_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                             "…": "...", " ": " "})
_FILLER = re.compile(r"\b(?:um+|uh+|uhm+|erm+|er|hmm+|you know)\b,?", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.{3,}|\[\s*\.{3}\s*\]")
_EDGE = " .,;:!?\"'()[]-"

_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = "january|february|march|april|june|july|august|september|october|november|december"
_TOKEN = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                       # email
    r"|https?://\S+|www\.\S+"                             # url
    r"|[$€£¥₹]?\d[\d,.:]*(?:\s?(?:k|m|bn|b|%|percent))?"  # number, money, percentage, clock time
    rf"|\b(?:{_WEEKDAYS}|{_MONTHS})\b"                    # weekday, month
    r"|\bmay(?=\s+\d)|(?<=\d\s)may\b",                    # "may" only as a month next to a day
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}\"'"


def normalise(text: str) -> str:
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", _FILLER.sub(" ", plain)).strip().casefold()


def locate(quote: str, norm_text: str) -> tuple[int, int] | None:
    """(start, end) of the quote in a normalised text, or None. An ellipsis splits it: each piece must be
    found, in order, all within ELLIPSIS_SPAN_CHARS. A quote under MIN_QUOTE_CHARS never matches."""
    pieces = [p.strip(_EDGE) for p in _ELLIPSIS.split(normalise(quote))]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < MIN_QUOTE_CHARS:
        return None
    first = pieces[0]
    at = norm_text.find(first)
    while at != -1:
        end = _rest_in_order(pieces[1:], norm_text, at + len(first), at + ELLIPSIS_SPAN_CHARS)
        if end is not None:
            return at, end
        at = norm_text.find(first, at + 1)
    return None


def _rest_in_order(pieces: list[str], norm_text: str, cursor: int, limit: int) -> int | None:
    for piece in pieces:
        found = norm_text.find(piece, cursor, limit)
        if found == -1:
            return None
        cursor = found + len(piece)
    return cursor


def contains_words(value: str, norm_text: str) -> bool:
    """`value` occurs in the normalised text on word boundaries."""
    wanted = normalise(value).strip(_EDGE)
    return bool(wanted) and re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", norm_text) is not None


def tokens(text: str) -> tuple[str, ...]:
    """Every number, amount, percentage, date word, email and URL in `text`."""
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(text or ""))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def ungrounded_tokens(text: str, norm_source: str) -> tuple[str, ...]:
    return tuple(t for t in tokens(text) if normalise(t) not in norm_source)
