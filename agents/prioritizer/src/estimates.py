"""Pure: an effort estimate written in a task ("(2h)", "~3 days", "half a day") mapped to effort 1-5.
A parsed estimate always beats the model's effort rating. Durations that read as a deadline ("in 3 days",
"within 2 weeks") are not estimates."""

from __future__ import annotations

import re
from dataclasses import dataclass

HOURS_PER_DAY = 8.0
DAYS_PER_WEEK = 5.0
# (upper bound in working hours, effort): <=1h, <=4h, <=2 days, <=6 days, more
_BANDS = ((1.0, 1), (4.0, 2), (2 * HOURS_PER_DAY, 3), (6 * HOURS_PER_DAY, 4))

_UNIT = r"(m|mins?|minutes?|h|hrs?|hours?|d|days?|w|wks?|weeks?)"
_NUMERIC = re.compile(rf"(?<![\w$.])(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*{_UNIT}\b", re.IGNORECASE)
_WORDS = re.compile(r"\b(half an hour|half a day|half-day|an hour|a couple of hours|a few hours|a day|a full day|"
                    r"a week)\b", re.IGNORECASE)
_WORD_HOURS = {"half an hour": 0.5, "half a day": 4.0, "half-day": 4.0, "an hour": 1.0, "a couple of hours": 2.0,
               "a few hours": 3.0, "a day": HOURS_PER_DAY, "a full day": HOURS_PER_DAY,
               "a week": DAYS_PER_WEEK * HOURS_PER_DAY}
_DEADLINE_LEAD = re.compile(r"\b(?:in|within|next|by|every|per|each|last|past|for the next|over the next)\s*~?\s*$",
                            re.IGNORECASE)


@dataclass(frozen=True)
class Estimate:
    effort: int
    phrase: str


def effort_for_hours(hours: float) -> int:
    for bound, effort in _BANDS:
        if hours <= bound:
            return effort
    return 5


def _hours(amount: float, unit: str) -> float:
    unit = unit.casefold()
    if unit.startswith("m"):
        return amount / 60
    if unit.startswith("h"):
        return amount
    if unit.startswith("d"):
        return amount * HOURS_PER_DAY
    return amount * DAYS_PER_WEEK * HOURS_PER_DAY


def parse(text: str) -> Estimate | None:
    """The first estimate in `text` (a range uses its upper end), or None."""
    text = text or ""
    for m in _NUMERIC.finditer(text):
        if _DEADLINE_LEAD.search(text[:m.start()]):
            continue
        amount = float(m.group(2) or m.group(1))
        return Estimate(effort_for_hours(_hours(amount, m.group(3))), m.group(0).strip())
    for m in _WORDS.finditer(text):
        if _DEADLINE_LEAD.search(text[:m.start()]):
            continue
        return Estimate(effort_for_hours(_WORD_HOURS[m.group(1).casefold()]), m.group(0).strip())
    return None
