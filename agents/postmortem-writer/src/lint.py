"""Pure: the blameless lint. Model-written prose must describe system conditions, not people.

Rejected: blame phrases ("human error", "forgot", "failed to", ...) and a speaker's name as the subject of a
negative verb ("Dan didn't roll back"). Names stay allowed in the timeline (who did what is a fact) and as
action-item owners; those are never linted."""

from __future__ import annotations

import re
from collections.abc import Iterable

BLAME_PHRASES = ("human error", "careless", "negligent", "should have known", "failed to", "forgot", "forgotten",
                 "mistake by", "fault of", "blame", "blamed", "to blame", "incompetent")
NEGATIVE_VERBS = r"(?:didn't|didnt|did not|failed|forgot|missed|broke|caused|ignored)"
NEUTRAL_TEMPLATE = "A change at {time} was followed by: {event}"
NEUTRAL_UNTIMED = "A change was followed by a {category} event."

_PHRASES = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in BLAME_PHRASES) + r")\b", re.IGNORECASE)


def _name_pattern(names: Iterable[str]) -> re.Pattern | None:
    words = set()
    for name in names:
        clean = name.strip().lstrip("@")
        if len(clean) >= 2:
            words.add(re.escape(clean))
            first = clean.split()[0]
            if len(first) >= 2:
                words.add(re.escape(first))
    if not words:
        return None
    alternation = "|".join(sorted(words, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternation})(?:'s)?\s+{NEGATIVE_VERBS}\b", re.IGNORECASE)


def violations(text: str, names: Iterable[str]) -> tuple[str, ...]:
    """What makes `text` not blameless (empty when it is fine)."""
    found = [m.group(0).casefold() for m in _PHRASES.finditer(text or "")]
    pattern = _name_pattern(names)
    if pattern is not None:
        found += [m.group(0) for m in pattern.finditer(text or "")]
    return tuple(found)


def is_blameless(text: str, names: Iterable[str]) -> bool:
    return not violations(text, names)


def neutral(time_text: str, event_text: str, category: str, names: Iterable[str]) -> str:
    """The fallback sentence when a repair still blames someone."""
    if time_text and event_text:
        sentence = NEUTRAL_TEMPLATE.format(time=time_text, event=event_text.rstrip("."))
        if is_blameless(sentence, names):
            return sentence + "."
    return NEUTRAL_UNTIMED.format(category=category or "follow-on")
