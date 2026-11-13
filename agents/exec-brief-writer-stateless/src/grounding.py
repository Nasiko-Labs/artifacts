"""Pure: tie each brief line to the pasted sections.

- `normalise` makes quote, dash, width, case, markdown-emphasis and whitespace differences invisible.
- `Sources.ground` keeps a cite only when its snippet (normalised, >= MIN_SNIPPET_CHARS) is a
  substring of the cited section; found in another section, the cite is re-pointed there.
- `Sources.ungrounded` lists the numbers, money, percentages, months, weekdays, emails, URLs and
  capitalised names in a line's wording that none of its cited sections contains.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

from brief import Cite
from sections import Section

MIN_SNIPPET_CHARS = 12

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
                         "–": "-", "—": "-", "‒": "-", "−": "-", "‐": "-",
                         "‑": "-", "…": "...", "*": " ", "`": " "})
_SPACE = re.compile(r"\s+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])[$€£]?\s?\d+(?:[.,]\d+)*(?:\s?%|\s?(?:percent|million|billion|thousand|bn|k|m|b)\b)?",
                     re.IGNORECASE)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CALENDAR = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|Mon(?:day)?|Tue(?:s|sday)?|Wed(?:nesday)?|Thu(?:rs|rsday)?|"
    r"Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)\b")
_CAPITALISED = re.compile(r"\b[A-Z][\w&-]*")
_SENTENCE_END = re.compile(r"[.!?]\s*$")
_UNITS = {"million": "m", "thousand": "k", "billion": "bn", "b": "bn", "percent": "%"}
_IGNORED_NAMES = frozenset({"i", "a", "an", "the", "high", "medium", "low"})


def normalise(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text or "").translate(_QUOTES).casefold()
    return _SPACE.sub(" ", folded).strip()


def _number_key(token: str) -> str:
    key = re.sub(r"[\s$€£,]", "", token.lower())
    match = re.match(r"([\d.]+)(.*)", key)
    if not match:
        return key
    return match.group(1) + _UNITS.get(match.group(2), match.group(2))


def _numbers(text: str) -> set[str]:
    return {_number_key(m.group()) for m in _NUMBER.finditer(text)}


def _is_sentence_start(text: str, index: int) -> bool:
    before = text[:index].rstrip(" \"'(")
    return not before or bool(_SENTENCE_END.search(before))


def _word_in(word: str, haystack: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(word)}(?!\w)", haystack) is not None


class Sources:
    """The pasted sections, normalised once per check."""

    def __init__(self, sections: Sequence[Section]):
        self._order = tuple(s.id for s in sections)
        self._norm = {s.id: normalise(s.text) for s in sections}
        self._numbers = {s.id: _numbers(s.text) for s in sections}

    def has(self, section_id: str) -> bool:
        return section_id in self._norm

    def norm(self, section_id: str) -> str:
        return self._norm.get(section_id, "")

    def ground(self, cites: Iterable[Cite]) -> tuple[Cite, ...]:
        kept: list[Cite] = []
        for cite in cites:
            snippet = normalise(cite.snippet)
            if len(snippet) < MIN_SNIPPET_CHARS:
                continue
            home = cite.section if snippet in self.norm(cite.section) else self._find(snippet)
            if home is not None and all(not (c.section == home and normalise(c.snippet) == snippet) for c in kept):
                kept.append(Cite(home, cite.snippet))
        return tuple(kept)

    def rank(self, cite: Cite) -> tuple[int, int]:
        """Where a cite sits in the paste: (section order, offset), for ordering lines."""
        index = self._order.index(cite.section) if cite.section in self._order else len(self._order)
        offset = self.norm(cite.section).find(normalise(cite.snippet))
        return index, max(offset, 0)

    def near(self, value: str, cite: Cite, distance: int) -> bool:
        """True when `value` appears in the cited section within `distance` chars of the snippet."""
        text, snippet = self.norm(cite.section), normalise(cite.snippet)
        offset = text.find(snippet)
        if offset < 0 or not value:
            return False
        window = text[max(0, offset - distance): offset + len(snippet) + distance]
        return normalise(value) in window

    def _find(self, snippet: str) -> str | None:
        return next((sid for sid in self._order if snippet in self._norm[sid]), None)

    def ungrounded(self, text: str, cites: Iterable[Cite]) -> list[str]:
        ids = {c.section for c in cites}
        haystack = " ".join(self.norm(i) for i in ids)
        numbers = set().union(*(self._numbers.get(i, set()) for i in ids)) if ids else set()
        missing = [m.group() for m in _NUMBER.finditer(text) if _number_key(m.group()) not in numbers]
        missing += [m.group() for m in (*_EMAIL.finditer(text), *_URL.finditer(text))
                    if normalise(m.group()) not in haystack]
        missing += [m.group() for m in _CALENDAR.finditer(text) if not _calendar_in(m.group(), haystack)]
        missing += [w for w in self._names(text) if not _word_in(normalise(w), haystack)]
        return missing

    @staticmethod
    def _names(text: str) -> list[str]:
        cleaned = _URL.sub(" ", _EMAIL.sub(" ", text))
        names = []
        for match in _CAPITALISED.finditer(cleaned):
            word = match.group()
            if _CALENDAR.fullmatch(word) or word.lower() in _IGNORED_NAMES:
                continue
            if not _is_sentence_start(cleaned, match.start()):
                names.append(word)
        return names


def _calendar_in(word: str, haystack: str) -> bool:
    prefix = word[:3].lower()
    if prefix not in _MONTHS and prefix not in _WEEKDAYS:
        return True
    return re.search(rf"(?<!\w){prefix}", haystack) is not None
