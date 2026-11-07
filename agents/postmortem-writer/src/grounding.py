"""Pure: is a quote, a name, a time or a number really in the pasted log? Both sides are normalised the same way.

Unlike a transcript summarizer, times are content here, so they are kept. Emoji (characters and `:shortcode:`)
are dropped, since chat copies carry them inconsistently."""

from __future__ import annotations

import re
import unicodedata

MIN_SNIPPET_CHARS = 12
ELLIPSIS_SPAN_CHARS = 600
NEAR_WINDOW_CHARS = 400

_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                             "…": "...", " ": " ", "→": "->"})
_SHORTCODE = re.compile(r":[a-z0-9_+-]{2,30}:")
_WHITESPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.{3,}|\[\s*\.{3}\s*\]")
_EDGE = " .,;:!?\"'()[]-*`"

_TOKEN = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                        # email
    r"|https?://\S+|www\.\S+"                              # url
    r"|(?<!\w)\d{1,2}:\d{2}(?::\d{2})?"                    # clock time
    r"|(?<!\w)[$€£¥₹]?\d[\d,.]*(?:%|\s?(?:k|m|bn|b|percent|ms|s|x)\b)?",  # number, money, percentage, latency
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}\"'"


def _drop_emoji(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "So")


def normalise(text: str) -> str:
    """Casefold, NFKC, straight quotes, plain dashes, no emoji, single spaces."""
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    plain = _SHORTCODE.sub(" ", _drop_emoji(plain).casefold())
    return _WHITESPACE.sub(" ", plain).strip()


def locate(snippet: str, norm_source: str) -> tuple[int, int] | None:
    """(start, end) of the snippet in the normalised source, or None. An ellipsis splits it: each piece must
    be found, in order, within ELLIPSIS_SPAN_CHARS. A snippet under MIN_SNIPPET_CHARS never matches."""
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


def near(value: str, norm_source: str, span: tuple[int, int], window: int = NEAR_WINDOW_CHARS) -> bool:
    """`value` occurs in the source within `window` chars of the span (either side)."""
    wanted = normalise(value).strip(_EDGE)
    if not wanted:
        return False
    start, end = span
    return wanted in norm_source[max(0, start - window):end + window]


def tokens(text: str) -> tuple[str, ...]:
    """Every number, time, amount, percentage, email and URL in `text`."""
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(text or ""))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def contains_token(token: str, norm_haystack: str) -> bool:
    """A token is present only as a whole: "3" is not in "300ms" and "12" is not in "11:12"."""
    wanted = normalise(token)
    pattern = rf"(?<![\w.:]){re.escape(wanted)}(?![\d:])"
    return re.search(pattern, norm_haystack) is not None


def ungrounded_tokens(text: str, norm_haystack: str) -> tuple[str, ...]:
    return tuple(t for t in tokens(text) if not contains_token(t, norm_haystack))
