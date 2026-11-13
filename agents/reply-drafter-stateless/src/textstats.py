"""Pure: the counting both the style metrics and the tone checks use, so a reply is measured exactly the
way the examples were."""

from __future__ import annotations

import math
import re

LOW_FACTOR = 0.5
HIGH_FACTOR = 1.6

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
_CONTRACTION = re.compile(r"\b[A-Za-z]+(?:n['’]t|['’](?:ll|re|ve|d|m))\b", re.IGNORECASE)
# 's is a contraction only for these words; anything else ("Sam's") is read as a possessive.
_S_CONTRACTION = re.compile(r"\b(?:it|that|what|there|here|let|he|she|who|where|how|when|everything|nothing)['’]s\b",
                            re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)|\n+")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")
BULLET = re.compile(r"^(\s*)(?:[-*•]|\d+[.)])\s+", re.MULTILINE)


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))


def contractions(text: str) -> int:
    return len(_CONTRACTION.findall(text or "")) + len(_S_CONTRACTION.findall(text or ""))


def sentence_count(text: str) -> int:
    pieces = [p for p in _SENTENCE_END.split(text or "") if _WORD.search(p)]
    return max(1, len(pieces))


def has_emoji(text: str) -> bool:
    return bool(EMOJI.search(text or ""))


def has_bullets(text: str) -> bool:
    return bool(BULLET.search(text or ""))


def length_bounds(avg_words: int) -> tuple[int, int]:
    """The accepted reply length around the examples' average: [0.5x, 1.6x]."""
    return math.floor(LOW_FACTOR * avg_words), math.ceil(HIGH_FACTOR * avg_words)


def allowed_exclamations(exclaim_rate: float, words: int) -> int:
    """0 when the examples have none; else max(1, ceil(rate x words / 100 x 1.5))."""
    if exclaim_rate <= 0:
        return 0
    return max(1, math.ceil(exclaim_rate * words / 100 * 1.5))
