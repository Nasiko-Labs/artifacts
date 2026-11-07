"""Pure: follow-ups on the stored classification, answered in code with no model call: only noise /
actionable / unclear, only <alert>, why is <alert> noise, show the numbers, show sources, top N to fix, what if
for = Nm, as json. None means "not a recognised follow-up"."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fires import parse_duration

DEFAULT_TOP = 3
MAX_TOP = 20
MAX_FOR_SECS = 24 * 3600
_ONLY = re.compile(r"\b(?:only|just|show(?: me)?|list)\s+(?:the\s+)?(noise|noisy|actionable|unclear)\b", re.IGNORECASE)
_TOP = re.compile(r"\btop\s*(\d{1,2})?\b|\b(?:what|which)(?: ones)? (?:should i|to) fix\b", re.IGNORECASE)
_NUMBERS = re.compile(r"\b(?:show|see|give)(?: me)?\s+(?:the\s+)?(?:numbers|stats|statistics|signals|counts)\b|"
                      r"^\s*(?:numbers|stats)\s*$", re.IGNORECASE)
_SOURCES = re.compile(r"\b(?:show|see)\s+(?:the\s+)?(?:sources|evidence|rows)\b|^\s*(?:sources|evidence)\s*$",
                      re.IGNORECASE)
_WHAT_IF = re.compile(r"\bwhat if\b.*?\bfor\b\s*(?:[:=]|to|is|of|was|raised to|=)?\s*"
                      r"(\d+(?:\.\d+)?\s*(?:s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hours?)?)\b|"
                      r"\b(?:raise|set|change|lengthen)\b.*?\bfor\b\s*(?:[:=]|to)?\s*"
                      r"(\d+(?:\.\d+)?\s*(?:s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hours?)?)\b", re.IGNORECASE)
_WHY = re.compile(r"^\s*why\b", re.IGNORECASE)
_DEFAULT = re.compile(r"^\s*(?:show (?:it|all|everything)|full (?:report|list)|all|again|summary)\s*\.?$",
                      re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str                       # only | group | numbers | sources | top | what_if | default
    label: str = ""
    group_key: str = ""
    top: int = DEFAULT_TOP
    for_secs: int = 0


def find_group(text: str, groups: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The stored group whose alert name appears in the message (longest name wins)."""
    folded = text.casefold()
    hits = [g for g in groups if re.search(rf"(?<![\w-]){re.escape(g['name'].casefold())}(?![\w-])", folded)]
    return max(hits, key=lambda g: (len(g["name"]), g["signals"]["n"])) if hits else None


def parse(text: str, result: Mapping[str, Any]) -> Command | None:
    message = (text or "").strip()
    if not message:
        return None
    what_if = _WHAT_IF.search(message)
    if what_if:
        raw = (what_if.group(1) or what_if.group(2)).strip()
        secs = parse_duration(raw, 60.0)
        if secs and 0 < secs <= MAX_FOR_SECS:
            return Command("what_if", for_secs=int(secs))
    group = find_group(message, result["groups"])
    if group is not None and (_WHY.search(message) or re.search(r"\b(?:only|just|show|about|details?)\b", message,
                                                                 re.IGNORECASE)):
        return Command("group", group_key=group["key"])
    only = _ONLY.search(message)
    if only:
        word = only.group(1).casefold()
        return Command("only", label="noise" if word.startswith("nois") else word)
    top = _TOP.search(message)
    if top:
        n = int(top.group(1)) if top.group(1) else DEFAULT_TOP
        return Command("top", top=max(1, min(n, MAX_TOP)))
    if _NUMBERS.search(message):
        return Command("numbers")
    if _SOURCES.search(message):
        return Command("sources")
    if _DEFAULT.match(message):
        return Command("default")
    if group is not None:
        return Command("group", group_key=group["key"])
    return None
