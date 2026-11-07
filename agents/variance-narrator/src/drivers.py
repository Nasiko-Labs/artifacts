"""Pure: driver binding. A driver comes only from the table's driver column or from a sentence of the
user's message that names the line (its full name, or a word of it no other line shares). Nothing else
is ever a cause."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from table import Row

TABLE, MESSAGE = "table", "message"
MAX_DRIVER_CHARS = 200
MIN_WORD_CHARS = 4
_STOP = {"cost", "costs", "total", "other", "expense", "expenses", "services", "fees", "line", "budget", "actual",
         "misc", "miscellaneous", "general", "spend", "items", "and", "with"}
_REQUEST = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:explain|write|narrate|give|show|make|draft|"
                      r"summari[sz]e|flag|use|threshold|period|currency|here|this is|below|attached|board|"
                      r"only|treat)\b", re.IGNORECASE)
_NOT_A_DRIVER = re.compile(r"\b(?:un)?favou?rable\b|\bthreshold\b|\bas json\b", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(?:[-*•]\s*)?(?:drivers?|notes?|comments?|context|why)\s*:\s*", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9][a-z0-9&'-]*")


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").casefold())


def unique_words(names: Sequence[str]) -> dict[str, set[str]]:
    counts: dict[str, int] = {}
    per_name = {n: {w for w in _words(n) if len(w) >= MIN_WORD_CHARS and w not in _STOP} for n in names}
    for words in per_name.values():
        for w in words:
            counts[w] = counts.get(w, 0) + 1
    return {n: {w for w in words if counts[w] == 1} for n, words in per_name.items()}


def mentions(text: str, names: Sequence[str]) -> list[str]:
    """Lines named in `text`: full-name matches first (longest wins), else unique-word matches."""
    plain = " ".join(_words(text))
    full = [n for n in names if _has(plain, " ".join(_words(n)))]
    full = [n for n in full if not any(n != o and _has(" ".join(_words(o)), " ".join(_words(n))) for o in full)]
    if full:
        return full
    unique = unique_words(names)
    return [n for n in names if any(_has(plain, w) for w in unique[n])]


def sharing(text: str, names: Sequence[str]) -> list[str]:
    """Lines sharing a significant word with `text`: the candidates when no line is named outright."""
    plain = " ".join(_words(text))
    return [n for n in names if any(_has(plain, w) for w in _words(n) if len(w) >= MIN_WORD_CHARS and w not in _STOP)]


def _has(haystack: str, needle: str) -> bool:
    return bool(needle) and re.search(rf"(?:^| ){re.escape(needle)}(?: |$)", haystack) is not None


def clean(sentence: str) -> str:
    text = " ".join(_LABEL.sub("", sentence or "").split()).strip(" -")
    return text[:MAX_DRIVER_CHARS]


def bind(rows: Sequence[Row], message: str, extra: Mapping[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """name -> (driver text, source). Drivers added later win; then the table column; then message sentences."""
    names = [r.name for r in rows if not r.is_total]
    bound = {r.name: (r.driver, TABLE) for r in rows if not r.is_total and r.driver}
    for name, text in (extra or {}).items():
        if name in names and text:
            bound[name] = (text, MESSAGE)
    for sentence in _SENTENCE_END.split(message or ""):
        text = clean(sentence)
        if not text or _REQUEST.match(text) or _NOT_A_DRIVER.search(text) or len(text.split()) < 2:
            continue
        for name in mentions(text, names):
            bound.setdefault(name, (text, MESSAGE))
    return bound


def supplied(bound: Mapping[str, tuple[str, str]]) -> list[str]:
    return [text for text, _ in bound.values() if text]
