"""Pure: who owns an action item, whether it is the user's, and the user's name when they tell it.

The model only says how the owner is referred to (`speaker`, `named` + a name, or null). Code resolves
it against the message and the thread's speakers, and never guesses: anything it cannot resolve is
"Not stated"."""

from __future__ import annotations

import re
from collections.abc import Sequence

import grounding as g
from model import NOT_STATED
from names import norm_name
from thread import UNKNOWN, Message

GROUP_WORDS = frozenset({"we", "us", "our", "ours", "team", "the team", "everyone", "everybody", "all", "all of us",
                         "someone", "somebody", "anyone", "anybody", "they", "them", "you", "you all", "y'all",
                         "folks", "guys", "nobody", "none", "tbd", "tbc", "n/a", "null", "unknown", "i", "me",
                         "not stated", "unassigned"})
MAX_NAME_WORDS = 3
FOLLOWUP_MAX_WORDS = 8

_NAME_WORDS = r"[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,2}"
_STRICT = (re.compile(r"\bmy[_ ]name\s*(?::|=|is)\s*(?P<n>[^\n,.;!?()]{1,40})", re.IGNORECASE),
           re.compile(rf"\bI(?:'|’)m\s+(?P<n>{_NAME_WORDS})"),
           re.compile(rf"\bI am\s+(?P<n>{_NAME_WORDS})"))
_RELAXED = re.compile(r"^\s*(?:i'm|i’m|im|i am|this is|it's|it is)\s+(?P<n>[\w'’-]+(?:\s+[\w'’-]+)?)\s*[.!]?\s*$",
                      re.IGNORECASE)
_NOT_A_NAME = frozenset({"not", "sure", "going", "back", "done", "here", "also", "in", "on", "out", "ok", "okay",
                         "fine", "happy", "sorry", "still", "just", "the", "a", "an", "good", "glad", "afraid",
                         "looking", "trying", "asking", "working", "confused", "new", "ready", "available", "free",
                         "interested", "curious", "wondering", "thinking", "in charge", "late", "away", "off"})


def resolve(owner_ref: object, owner_name: object, message: Message, quote: str,
            speakers: Sequence[str]) -> str:
    ref = str(owner_ref or "").strip().casefold()
    if ref == "speaker":
        return message.speaker if message.speaker != UNKNOWN else NOT_STATED
    if ref != "named":
        return NOT_STATED
    name = " ".join(str(owner_name or "").split()).strip(" .,:;@")
    if not name or norm_name(name) in GROUP_WORDS:
        return NOT_STATED
    speaker = match_speaker(name, speakers)
    if speaker is not None:
        return speaker
    if _whole_word(name, message.text) or _whole_word(name, quote):
        return name
    return NOT_STATED


def match_speaker(name: str, speakers: Sequence[str]) -> str | None:
    """The speaker's full label: an exact (normalised) match, else a unique first-name match."""
    wanted = norm_name(name)
    if not wanted:
        return None
    for speaker in speakers:
        if norm_name(speaker) == wanted:
            return speaker
    if len(wanted.split()) != 1:
        return None
    same_first = [s for s in speakers if _first(s) == wanted]
    return same_first[0] if len(same_first) == 1 else None


def is_mine(owner: str, my_name: str, speakers: Sequence[str]) -> bool | None:
    """True / False when both the owner and the user's name are known; None otherwise."""
    if not my_name or not owner or owner == NOT_STATED:
        return None
    if norm_name(owner) == norm_name(my_name):
        return True
    first = _first(my_name)
    if first and first == _first(owner):
        return sum(1 for s in speakers if _first(s) == first) == 1
    return False


def name_in_instruction(text: str) -> str:
    """`my_name: X`, `my name is X`, `I'm X`, `I am X` (X capitalised) in the request, else ""."""
    for pattern in _STRICT:
        for match in pattern.finditer(text or ""):
            name = _clean(match.group("n"))
            if name:
                return name
    return ""


def name_in_followup(text: str, speakers: Sequence[str]) -> str:
    """A short follow-up that only states the user's name. Lower case is accepted for a known speaker."""
    plain = " ".join((text or "").split())
    if not plain or len(plain.split()) > FOLLOWUP_MAX_WORDS:
        return ""
    strict = name_in_instruction(plain)
    if strict and len(plain.split()) <= len(strict.split()) + 4:
        return strict
    relaxed = _RELAXED.match(plain)
    if relaxed:
        found = match_speaker(_clean(relaxed.group("n")), speakers)
        return found or ""
    return ""


def _clean(raw: str) -> str:
    words = " ".join(str(raw or "").split()).strip(" .,:;!?'\"").split()[:MAX_NAME_WORDS]
    name = " ".join(words)
    if not name or norm_name(name) in _NOT_A_NAME or norm_name(words[0]) in _NOT_A_NAME:
        return ""
    return name


def _first(name: str) -> str:
    words = norm_name(name).split()
    return words[0] if words else ""


def _whole_word(name: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(g.normalise(name))}(?!\w)", g.normalise(text)) is not None
