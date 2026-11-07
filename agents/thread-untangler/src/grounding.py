"""Pure: is a quote, a name or a number really in the thread? Both sides are normalised the same way."""

from __future__ import annotations

import re
import unicodedata

MIN_SNIPPET_CHARS = 12
ELLIPSIS_SPAN_CHARS = 600

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


def tokens(text: str) -> tuple[str, ...]:
    """Every number, amount, percentage, date word, email and URL in `text`."""
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(text or ""))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def ungrounded_tokens(text: str, norm_source: str) -> tuple[str, ...]:
    """The tokens of `text` that do not appear in the source."""
    return tuple(t for t in tokens(text) if normalise(t) not in norm_source)


_WORD = re.compile(r"[a-z][a-z'-]+")
_CAPITALISED = re.compile(r"\b[A-Z][\w'-]+")
_SENTENCE_END = re.compile(r"(?<=[.!?:;])\s+")
STOPWORDS = frozenset({
    "about", "above", "after", "again", "against", "also", "among", "another", "anyone", "anything", "around",
    "because", "been", "before", "being", "below", "between", "both", "cannot", "could", "does", "doing",
    "done", "down", "during", "each", "either", "else", "even", "ever", "every", "from", "further", "going",
    "gonna", "have", "having", "here", "hers", "herself", "himself", "into", "itself", "just", "know", "like",
    "make", "more", "most", "much", "must", "myself", "need", "only", "other", "ought", "ours", "over", "same",
    "shall", "should", "some", "such", "than", "that", "thats", "their", "theirs", "them", "then", "there",
    "these", "they", "this", "those", "through", "till", "under", "until", "very", "want", "were", "what",
    "when", "where", "which", "while", "whom", "whose", "will", "with", "would", "your", "yours", "yourself",
    "okay", "sure", "yeah", "thanks", "think", "thing", "things", "really", "still", "well", "maybe", "let's",
    "lets"
})


def content_words(text: str) -> frozenset[str]:
    """Words of 4+ letters that are not stopwords, normalised."""
    return frozenset(w.strip("'-") for w in _WORD.findall(normalise(text))
                     if len(w.strip("'-")) >= 4 and w.strip("'-") not in STOPWORDS)


STARTERS = frozenset({
    "a", "an", "the", "it", "its", "they", "he", "she", "we", "i", "you", "yes", "no", "nobody", "someone",
    "everyone", "all", "one", "none", "not", "after", "as", "at", "by", "for", "in", "on", "to", "so", "but",
    "and", "or", "if", "per", "according"
})


def ungrounded_names(text: str, norm_source: str) -> tuple[str, ...]:
    """Capitalised words of `text` that do not appear in the source. A sentence's first word counts too,
    unless it is a common opener ("The", "They", "After")."""
    found = [m.group(0) for sentence in _SENTENCE_END.split(text or "")
             for m in _CAPITALISED.finditer(sentence.strip())
             if m.start() > 0 or m.group(0).casefold() not in STOPWORDS | STARTERS]
    return tuple(w for w in found if normalise(w) not in norm_source)


def is_grounded(text: str, norm_source: str) -> bool:
    """No number, date word, email, URL or name in `text` that the source does not contain."""
    return not ungrounded_tokens(text, norm_source) and not ungrounded_names(text, norm_source)
