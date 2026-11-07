"""Pure: is a quote, a number or a name really in the plan or its context? Both sides are normalised the
same way."""

from __future__ import annotations

import re
import unicodedata

_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                             "…": "...", " ": " "})
_WHITESPACE = re.compile(r"\s+")
_EDGE = " .,;:!?\"'()[]-`"

_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = ("january|february|march|april|june|july|august|september|october|november|december|"
           "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_TOKEN = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                       # email
    r"|https?://\S+|www\.\S+"                             # url
    r"|[$€£¥₹]?\d[\d,.]*(?:%|\s?(?:k|m|bn|b|percent)\b)?"  # number, money, percentage
    rf"|\b(?:{_WEEKDAYS}|{_MONTHS})\b"                    # weekday, month
    r"|\bmay(?=\s+\d)|(?<=\d\s)may\b",                    # "may" only as a month next to a day
    re.IGNORECASE,
)
_SENTENCES = re.compile(r"[.!?:;]\s+|\n+")
_WORD = re.compile(r"[A-Za-z][\w'-]*")
_CAPITALISED = re.compile(r"[A-Z][a-z]{2,}")  # all-caps acronyms (API, QA) are not treated as names
_TRAILING = ".,;:!?)]}\"'"


def normalise(text: str) -> str:
    """Casefold, NFKC, straight quotes, plain dashes, single spaces."""
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", plain).strip().casefold()


def clean_ref(snippet: str) -> str:
    """A model quote without wrapping quotes or a leading 'L12:' label."""
    text = re.sub(r"^\s*\(?L\d+\)?\s*[:.-]?\s*", "", snippet or "")
    return normalise(text).strip(_EDGE)


def tokens(text: str) -> tuple[str, ...]:
    """Every number, amount, percentage, date word, email and URL in `text`."""
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(text or ""))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def names(text: str) -> tuple[str, ...]:
    """Capitalised words that are not the first word of a sentence."""
    found: list[str] = []
    for sentence in _SENTENCES.split(text or ""):
        words = _WORD.findall(sentence)
        found += [w for w in words[1:] if _CAPITALISED.fullmatch(w.split("'")[0])]
    return tuple(found)


def ungrounded(text: str, norm_ground: str) -> tuple[str, ...]:
    """The numbers, dates, emails, links and names in `text` that the plan and context never mention."""
    found = [t for t in tokens(text) if not _bounded_in(normalise(t), norm_ground)]
    found += [n for n in names(text) if not re.search(rf"\b{re.escape(n.casefold())}", norm_ground)]
    return tuple(found)


def _bounded_in(token: str, norm_ground: str) -> bool:
    """`token` in the ground text, not as part of a longer number or word ("5" is not in "15", "jan" is not
    in "january")."""
    before = r"(?<!\d)" if token[:1].isdigit() else r"(?<![a-z])" if token[:1].isalpha() else ""
    after = r"(?!\d)" if token[-1:].isdigit() else r"(?![a-z])" if token[-1:].isalpha() else ""
    return re.search(f"{before}{re.escape(token)}{after}", norm_ground) is not None
