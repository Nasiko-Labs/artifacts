"""Pure: a pasted tier/plan/segment value -> enterprise | business | pro | free | unknown."""

from __future__ import annotations

import re

_TABLE = (
    ("enterprise", re.compile(r"\b(?:enterprise|ent|premium|vip|platinum|strategic)\b")),
    ("business", re.compile(r"\b(?:business|team|standard|growth)\b")),
    ("pro", re.compile(r"\b(?:pro|professional|starter|basic|plus)\b")),
    ("free", re.compile(r"\b(?:free|trial|freemium|community)\b")),
)


def normalise(value: str | None) -> str:
    text = (value or "").strip().casefold()
    if not text:
        return "unknown"
    for tier, pattern in _TABLE:
        if pattern.search(text):
            return tier
    return "unknown"
