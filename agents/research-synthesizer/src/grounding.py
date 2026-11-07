"""Pure: is a quote, a name or a number really in the source? Both sides are normalised the same way.
Also the word-level measures the checks use: content words, overlap and 8-word shingles."""

from __future__ import annotations

import re
import unicodedata

MIN_SNIPPET_CHARS = 12
ELLIPSIS_SPAN_CHARS = 600
SHINGLE_WORDS = 8

_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                             "…": "...", " ": " "})
_WHITESPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.{3,}|\[\s*\.{3}\s*\]")
_EDGE = " .,;:!?\"'()[]-"
_MONTHS = "january|february|march|april|june|july|august|september|october|november|december"
_TOKEN = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                       # email
    r"|https?://\S+|www\.\S+"                             # url
    r"|(?<![A-Za-z_])[$€£¥₹]?\d[\d,.]*(?:\s?(?:k|m|bn|b|%|percent))?"   # number, money, percentage
    rf"|\b(?:{_MONTHS})\b",                               # month
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d")
_TRAILING = ".,;:!?)]}\"'"
_WORD = re.compile(r"[a-z0-9][a-z0-9'-]*")
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "so", "of", "to", "in", "on", "at", "by", "for",
    "with", "from", "into", "onto", "about", "as", "is", "are", "was", "were", "be", "been", "being", "it",
    "its", "this", "that", "these", "those", "there", "their", "they", "them", "we", "our", "us", "you", "your",
    "i", "me", "my", "he", "she", "his", "her", "not", "no", "do", "does", "did", "done", "have", "has", "had",
    "will", "would", "can", "could", "should", "may", "might", "must", "also", "just", "only", "very", "more",
    "most", "less", "much", "many", "some", "any", "all", "each", "every", "what", "which", "who", "whom",
    "whose", "when", "where", "why", "how", "up", "out", "over", "under", "again", "said", "says", "say", "per",
    "via", "vs",
})


def normalise(text: str) -> str:
    """Casefold, NFKC, straight quotes, plain dashes, single spaces."""
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", plain).strip().casefold()


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
    """Every number, amount, percentage, month, email and URL in `text`."""
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(text or ""))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def numbers(text: str) -> frozenset[str]:
    """The normalised number-bearing tokens of `text` (amounts, percentages, years, counts)."""
    return frozenset(normalise(t) for t in tokens(text) if _NUMBER.search(t))


def ungrounded_tokens(text: str, norm_source: str) -> tuple[str, ...]:
    """The tokens of `text` that do not appear in the source."""
    return tuple(t for t in tokens(text) if normalise(t) not in norm_source)


def content_words(text: str) -> frozenset[str]:
    """Lower-cased words that carry meaning, with a plain plural 's' dropped."""
    words = _WORD.findall(normalise(text))
    return frozenset(_stem(w) for w in words if w not in STOPWORDS and len(w) > 2)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def overlap(a: str, b: str) -> float:
    """Shared content words over the smaller set (0 when either side has none)."""
    wa, wb = content_words(a), content_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def shingles(text: str, size: int = SHINGLE_WORDS) -> frozenset[tuple[str, ...]]:
    words = _WORD.findall(normalise(text))
    return frozenset(tuple(words[i:i + size]) for i in range(max(0, len(words) - size + 1)))
