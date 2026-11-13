"""Pure: the user's controls, parsed in code from the request words (never from the draft)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

VOICES = ("neutral", "casual", "formal")

_EM_DASHES = re.compile(r"\bkeep (?:my |the |all )?(?:em[- ]?)?dash(?:es)?\b")
_STRUCTURE = re.compile(r"\bkeep (?:my |the |all )?(?:bullet(?: point)?s?|structure|lists?|formatting|layout)\b")
_HEADINGS = re.compile(r"\bkeep (?:my |the |all )?headings?\b")
_CASUAL = re.compile(r"\b(?:more )?(?:casual|informal|relaxed|conversational)\b|\bless formal\b|\bfriendlier\b")
_FORMAL = re.compile(r"\b(?:more )?(?:formal|professional)\b|\bless casual\b")
_NEUTRAL = re.compile(r"\bneutral\b")
_SHORTER = re.compile(r"\bshorter\b|\btighten(?: it| this)?(?: up)?\b|\bmore concise\b|\btrim it\b")
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b")


@dataclass(frozen=True)
class Options:
    keep_em_dashes: bool = False
    keep_structure: bool = False
    keep_headings: bool = False
    voice: str = "neutral"
    shorter: bool = False
    max_words: int = 0   # set in code for "shorter" (never parsed from words): the result's word cap, 0 = none


@dataclass(frozen=True)
class Parsed:
    options: Options
    changed: bool        # the words set at least one option
    as_json: bool


def parse(text: str, base: Options | None = None) -> Parsed:
    """Options named in `text`, layered over `base` (a follow-up keeps what the last pass used)."""
    plain = " ".join((text or "").casefold().replace("’", "'").split())
    current = base or Options()
    updates: dict[str, Any] = {}
    if _EM_DASHES.search(plain):
        updates["keep_em_dashes"] = True
    if _STRUCTURE.search(plain):
        updates["keep_structure"] = True
    if _HEADINGS.search(plain):
        updates["keep_headings"] = True
    voice = _voice(plain)
    if voice:
        updates["voice"] = voice
    if _SHORTER.search(plain):
        updates["shorter"] = True
    return Parsed(replace(current, **updates), bool(updates), bool(_JSON.search(plain)))


def _voice(plain: str) -> str | None:
    if _CASUAL.search(plain):
        return "casual"
    if _FORMAL.search(plain):
        return "formal"
    return "neutral" if _NEUTRAL.search(plain) else None
