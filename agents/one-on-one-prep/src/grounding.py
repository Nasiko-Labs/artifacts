"""Pure: is a quote, a number or a name really in the pasted part it cites? Both sides are normalised the
same way."""

from __future__ import annotations

import re
import unicodedata

MIN_SNIPPET_CHARS = 12
_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"',
                             "”": '"', "„": '"', "‐": "-", "‑": "-", "‒": "-",
                             "–": "-", "—": "-", "―": "-", "−": "-", "…": "...",
                             " ": " "})
_WHITESPACE = re.compile(r"\s+")
_EDGE = " .,;:!?\"'()[]-`*"
_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = ("january|february|march|april|june|july|august|september|october|november|december|"
           "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_TOKEN = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+|https?://\S+|www\.\S+"
    r"|\b[A-Z]{2,10}-\d+\b"                                 # ticket id
    r"|[$€£¥₹]?\d[\d,.]*(?:%|\s?(?:k|m|bn|b|percent)\b)?"
    rf"|\b(?:{_WEEKDAYS}|{_MONTHS})\b",
    re.IGNORECASE,
)
_SENTENCES = re.compile(r"[.!?:;]\s+|\n+")
_WORD = re.compile(r"[A-Za-z][\w'-]*")
_CAPITALISED = re.compile(r"[A-Z][a-z]{2,}")
_TRAILING = ".,;:!?)]}\"'"


def normalise(text: str) -> str:
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", plain).strip().casefold()


def clean_quote(snippet: str) -> str:
    return " ".join((snippet or "").translate(_LOOKALIKES).split()).strip(_EDGE)


def in_source(snippet: str, source: str) -> bool:
    """A verbatim quote of `source` (normalised), at least MIN_SNIPPET_CHARS long."""
    quote = normalise(clean_quote(snippet))
    return len(quote) >= MIN_SNIPPET_CHARS and quote in normalise(source)


def tokens(text: str) -> tuple[str, ...]:
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(text or ""))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def names(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for sentence in _SENTENCES.split(text or ""):
        words = _WORD.findall(sentence)
        found += [w for w in words[1:] if _CAPITALISED.fullmatch(w.split("'")[0])]
    return tuple(found)


def ungrounded(text: str, ground: str) -> tuple[str, ...]:
    """Numbers, dates, ids, links and names in `text` that `ground` never mentions."""
    norm = normalise(ground)
    found = [t for t in tokens(text) if not _bounded_in(normalise(t), norm)]
    found += [n for n in names(text) if not re.search(rf"\b{re.escape(n.casefold())}", norm)]
    return tuple(found)


def _bounded_in(token: str, norm: str) -> bool:
    before = r"(?<!\d)" if token[:1].isdigit() else r"(?<![a-z])" if token[:1].isalpha() else ""
    after = r"(?!\d)" if token[-1:].isdigit() else r"(?![a-z])" if token[-1:].isalpha() else ""
    return re.search(f"{before}{re.escape(token)}{after}", norm) is not None
