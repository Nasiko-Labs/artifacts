"""Pure: rating text as an interviewer wrote it → a point on one -2..+2 scale, in code.

Words: strong no = -2, no / lean no = -1, neutral = 0, lean yes / yes = 1, strong yes = 2 (the
"hire" family maps to the same points). Numbers on a 1-5 or 1-4 scale are centred and scaled to
-2..+2; a bare number is read on a 1-5 scale. The original text is always what is displayed; the
point is used only to find spreads, never shown, summed or averaged. Unknown text is not rated.
"""

from __future__ import annotations

import re

NOT_STATED = "not stated"
SCALE_MIN, SCALE_MAX = -2.0, 2.0
_DEFAULT_SCALE = 5

# Longest phrases first, so "strong no hire" is not read as "no".
_WORDS: tuple[tuple[str, float], ...] = (
    (r"strong(?:ly)?\s+no(?:\s+hire)?|definitely\s+not|strong\s+reject", -2.0),
    (r"strong(?:ly)?\s+(?:yes|hire|inclined)|definitely(?:\s+yes|\s+hire)?", 2.0),
    (r"lean(?:ing)?\s+no(?:\s+hire)?|weak\s+no|leaning\s+against", -1.0),
    (r"lean(?:ing)?\s+(?:yes|hire)|weak\s+yes|leaning\s+towards?", 1.0),
    (r"thumbs?\s+down", -1.0),
    (r"thumbs?\s+up", 1.0),
    (r"no\s+hire|no", -1.0),
    (r"yes|hire|inclined", 1.0),
    (r"neutral|mixed|on\s+the\s+fence|unsure|undecided", 0.0),
)
_WORD_RES = tuple((re.compile(rf"^(?:{pattern})\b"), point) for pattern, point in _WORDS)
_NUMBER = re.compile(r"^(\d(?:\.\d)?)\s*(?:(?:/|out\s+of|of)\s*([45]))?\b")
_LEAD = re.compile(r"^[\s\-–:*\"'(\[]+")


def normalise(rating_text: str | None) -> float | None:
    """The -2..+2 point for a rating as written, or None when it is not a rating this table knows."""
    text = _LEAD.sub("", (rating_text or "").casefold()).strip()
    if not text:
        return None
    number = _NUMBER.match(text)
    if number:
        return _from_number(float(number.group(1)), int(number.group(2) or _DEFAULT_SCALE))
    for pattern, point in _WORD_RES:
        if pattern.match(text):
            return point
    return None


def _from_number(score: float, scale: int) -> float | None:
    if not 1 <= score <= scale:
        return None
    midpoint = (1 + scale) / 2
    return round((score - midpoint) * (SCALE_MAX / (scale - midpoint)), 2)


def display(rating_text: str | None) -> str:
    """What the debrief shows: the rating exactly as written, or `not stated`."""
    text = " ".join((rating_text or "").split())
    return text if text else NOT_STATED


def is_rating(text: str) -> bool:
    return normalise(text) is not None
