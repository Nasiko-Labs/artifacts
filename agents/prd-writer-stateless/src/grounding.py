"""Pure: is a snippet, a number, a date or a name really in the brief? Both sides are normalised the same way.

The grounding corpus is the brief, the user's instruction and the user's answers to this agent's
questions; org-policy parts are never part of it."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

MIN_SNIPPET_CHARS = 8
ELLIPSIS_SPAN_CHARS = 600

_LOOKALIKES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
                             "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                             "…": "...", " ": " "})
_WHITESPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\.{3,}|\[\s*\.{3}\s*\]")
_EDGE = " .,;:!?\"'()[]-"
_PLACEHOLDER = re.compile(r"\[[^\]\n]{0,40}\?\s*\]|\[vague: define\]")

_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_MONTHS = "january|february|march|april|june|july|august|september|october|november|december"
_TOKEN = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"                       # email
    r"|https?://\S+|www\.\S+"                             # url
    r"|[$€£¥₹]?\d(?:[\d,.]*\d)?(?:\s?(?:k|m|bn|b|percent|pct)\b|%)?"  # number, money, percentage
    rf"|\b(?:{_WEEKDAYS}|{_MONTHS})\b"                    # weekday, month
    r"|\bmay(?=\s+\d)|(?<=\d\s)may\b",                    # "may" only as a month next to a day
    re.IGNORECASE,
)
_TRAILING = ".,;:!?)]}\"'"
_NUMBER = re.compile(r"^([$€£¥₹]?)(\d(?:[\d,]*\d)?(?:\.\d+)?)\s?(k|m|bn|b|percent|pct|%)?$")
_SCALE = {"k": 1e3, "m": 1e6, "bn": 1e9, "b": 1e9}
_PERCENT = ("percent", "pct", "%")
# A capitalised word that does not open a sentence: a name, a product, a company.
_CAPITALISED = re.compile(r"(?<![.!?:]\s)(?<!^)(?<![\w'-])([A-Z][\w'&-]*[A-Za-z0-9])")
_GENERIC_CAPS = frozenset({"i", "prd", "api", "apis", "ui", "ux", "mvp", "id", "ids", "faq", "url", "urls", "ok",
                           "the", "a", "an", "users", "user", "r"})


def normalise(text: str) -> str:
    """Casefold, NFKC, straight quotes, plain dashes, single spaces."""
    plain = unicodedata.normalize("NFKC", text or "").translate(_LOOKALIKES)
    return _WHITESPACE.sub(" ", plain).strip().casefold()


def strip_placeholders(text: str) -> str:
    return _PLACEHOLDER.sub(" ", text or "")


def locate(snippet: str, norm_source: str) -> bool:
    """The snippet is in the normalised source. An ellipsis splits it: each piece must be found, in
    order, within ELLIPSIS_SPAN_CHARS. Too short a snippet never matches."""
    pieces = [p.strip(_EDGE) for p in _ELLIPSIS.split(normalise(snippet))]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < MIN_SNIPPET_CHARS:
        return False
    at = norm_source.find(pieces[0])
    while at != -1:
        cursor, limit, ok = at + len(pieces[0]), at + ELLIPSIS_SPAN_CHARS, True
        for piece in pieces[1:]:
            found = norm_source.find(piece, cursor, limit)
            if found == -1:
                ok = False
                break
            cursor = found + len(piece)
        if ok:
            return True
        at = norm_source.find(pieces[0], at + 1)
    return False


def tokens(text: str) -> tuple[str, ...]:
    """Every number, amount, percentage, date word, email and URL in `text` (placeholders excluded)."""
    found = (m.group(0).rstrip(_TRAILING) for m in _TOKEN.finditer(strip_placeholders(text)))
    return tuple(t for t in found if t and any(c.isalnum() for c in t))


def names(text: str) -> tuple[str, ...]:
    """Capitalised words that do not open a sentence (generic acronyms excluded)."""
    body = strip_placeholders(text).strip()
    return tuple(w for w in _CAPITALISED.findall(body) if w.casefold() not in _GENERIC_CAPS)


def canonical(token: str) -> str | None:
    """One spelling per number: "30 percent" = "30 pct" = "30%", "1k" = "1,000" = "1000", "$30k" = "$30,000".
    None for anything that is not a plain number, amount or percentage."""
    found = _NUMBER.match(normalise(token))
    if found is None:
        return None
    currency, digits, unit = found.groups()
    value = float(digits.replace(",", "")) * _SCALE.get(unit or "", 1)
    number = f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{currency}{number}{'%' if unit in _PERCENT else ''}"


@lru_cache(maxsize=64)
def _source_numbers(norm_source: str) -> dict[str, str]:
    """canonical form -> the source's own spelling, for every number in the (normalised) source."""
    numbers: dict[str, str] = {}
    for token in tokens(norm_source):
        form = canonical(token)
        if form is not None:
            numbers.setdefault(form, token)
    return numbers


def source_form(token: str, norm_source: str) -> str | None:
    """How the source spells this token: itself when it is there verbatim, an equivalent number form, or None."""
    form = canonical(token)
    if form is not None:
        return _source_numbers(norm_source).get(form)
    return token if normalise(token) in norm_source else None


def ungrounded(text: str, norm_source: str) -> tuple[str, ...]:
    """The numbers, dates, emails, links and names of `text` that the source does not contain (numbers in any
    equivalent form)."""
    wanted = (*tokens(text), *names(text))
    return tuple(t for t in wanted if source_form(t, norm_source) is None)


def has_placeholder(text: str) -> bool:
    return bool(_PLACEHOLDER.search(text or ""))


def placeholders(text: str) -> tuple[str, ...]:
    return tuple(m.group(0) for m in _PLACEHOLDER.finditer(text or ""))
