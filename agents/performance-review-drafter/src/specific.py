"""Pure: whether an evidence item is concrete enough to use. The model's `specific` is re-checked here: the
item must name a number, a date, a proper noun or system name, or be 12+ words with a verb; otherwise it is
forced false ("great attitude" is too general to use)."""

from __future__ import annotations

import re

from evidence import Evidence

MIN_WORDS_WITH_VERB = 12
_NUMBER = re.compile(r"\d")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9#\-_.]*")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|[\"“(]\s*)$")
_SYSTEM = re.compile(r"\b[a-z]+[A-Z_][A-Za-z_]*\b|\b[A-Za-z]+-[A-Za-z]+-\w+\b|#\w+|\b[A-Z]{2,}\b")
_VERB = re.compile(
    r"\b(?:\w+ed|built|led|ran|wrote|made|took|found|gave|got|kept|met|brought|caught|cut|set|shipped|ships|"
    r"leads|owns|writes|reviews|fixes|drives|helps|explains|mentors|presents|delivers|runs|missed|misses|"
    r"left|forgot|spoke|told|said|sent|did|does|lost|won|began|became|taught|thought)\b", re.IGNORECASE)
_COMMON_CAPS = {"I", "A", "An", "The", "He", "She", "They", "We", "It", "This", "That", "On", "In", "At",
                "For", "With", "And", "But", "Our", "My", "His", "Her", "Their", "Thanks", "Great"}


def proper_nouns(text: str, ignore: set[str] = frozenset()) -> list[str]:
    """Capitalised words that are not at a sentence start (names, systems, products), minus `ignore`."""
    found: list[str] = []
    for m in _WORD.finditer(text or ""):
        word = m.group(0).rstrip(".-_")
        if not word[:1].isupper() or word in _COMMON_CAPS or word.casefold() in ignore:
            continue
        if _SENTENCE_START.search(text[:m.start()]):
            continue
        found.append(word)
    return found


def is_specific(item: Evidence, person: str = "") -> bool:
    text = item.text
    if item.date or _NUMBER.search(text) or _SYSTEM.search(text):
        return True
    ignore = {person.casefold()} if person else set()
    if proper_nouns(text, ignore):
        return True
    return len(text.split()) >= MIN_WORDS_WITH_VERB and bool(_VERB.search(text))


def has_metric(item: Evidence) -> bool:
    return item.source == "metric" or bool(_NUMBER.search(item.text))
