"""Pure: a one-line ask -> what to read from the desk (window, topic, min, top), or the fixed "can't create"
answer. Parsed in code; the model never decides a window or a threshold."""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
from candidates import MIN_DEFAULT, MIN_RANGE, TOP_DEFAULT, TOP_RANGE, clamp

WINDOW_DEFAULT = 30
WINDOW_RANGE = (7, 90)
TOPIC_MAX_CHARS = 40

_LIVE_ASK = re.compile(
    r"\b(?:macros?|canned|saved repl\w*|keep (?:typing|writing|sending|giving)|by hand|recurring|repeated|"
    r"repetitive|same answers?|common (?:answers|replies)|(?:answers|replies) (?:we|our|agents)|gaps?)\b",
    re.IGNORECASE)
_CREATE = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?:create|add|save|publish|set up|upload|install)\b"
                     r".*\b(?:macros?|it|this|that|them|these|draft)\b", re.IGNORECASE)
_DAYS = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s*(days?|weeks?|months?)\b", re.IGNORECASE)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
_NAMED = re.compile(r"\b(?:last|past|this)\s+(week|month|quarter)\b", re.IGNORECASE)
_NAMED_DAYS = {"week": 7, "month": 30, "quarter": 90}
_TOPIC = re.compile(r"\btopic\s*[:=]\s*([\w -]{2,40})|\bonly\s+([\w-]{2,30}(?:\s[\w-]{2,30})?)\s+tickets\b|"
                    r"\bfor the\s+([\w-]{2,30}(?:\s[\w-]{2,30}){0,2})\s+question\b", re.IGNORECASE)


@dataclass(frozen=True)
class LiveAsk:
    text: str
    days: int = WINDOW_DEFAULT
    topic: str = ""
    min_freq: int = MIN_DEFAULT
    top: int = TOP_DEFAULT
    window_clamped: bool = False


def wants_create(text: str) -> bool:
    return bool(_CREATE.search(text or ""))


def is_live_ask(text: str) -> bool:
    return bool(_LIVE_ASK.search(text or ""))


def _days(text: str) -> int | None:
    match = _DAYS.search(text)
    if match:
        unit = match.group(2).casefold().rstrip("s")
        return int(match.group(1)) * _UNIT_DAYS[unit]
    named = _NAMED.search(text)
    return _NAMED_DAYS[named.group(1).casefold()] if named else None


def parse(text: str) -> LiveAsk:
    asked = _days(text)
    days = clamp(asked, WINDOW_DEFAULT, WINDOW_RANGE)
    topic_match = _TOPIC.search(text)
    topic = next((g for g in topic_match.groups() if g), "").strip()[:TOPIC_MAX_CHARS] if topic_match else ""
    command = followup.parse(text)
    min_freq = clamp(command.min_freq if command else None, MIN_DEFAULT, MIN_RANGE)
    top = clamp(command.top if command else None, TOP_DEFAULT, TOP_RANGE)
    return LiveAsk(text=text, days=days, topic=topic, min_freq=min_freq, top=top,
                   window_clamped=asked is not None and asked != days)
