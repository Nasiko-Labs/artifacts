"""Pure: which reviews a turn analyses. A requested window ("last 30 days") excludes dated reviews outside
it (undated ones are kept and counted); then at most `cap` reviews, newest first, then lowest rating."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from normalise import to_date
from records import Review

_DAYS = re.compile(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", re.IGNORECASE)
_NAMED = {"last week": 7, "past week": 7, "last month": 30, "past month": 30}


@dataclass(frozen=True)
class Selection:
    reviews: tuple[Review, ...]
    window_start: date | None = None
    excluded_by_window: int = 0
    undated_in_window: int = 0
    capped: int = 0


def window_days(text: str) -> int | None:
    found = _DAYS.search(text or "")
    if found:
        return max(1, int(found.group(1)))
    lowered = (text or "").casefold()
    return next((days for phrase, days in _NAMED.items() if phrase in lowered), None)


def select(reviews: Sequence[Review], text: str, today: date, cap: int) -> Selection:
    days = window_days(text)
    start = today - timedelta(days=days) if days else None
    kept = list(reviews)
    excluded = undated = 0
    if start is not None:
        dated = [(r, to_date(r.date)) for r in kept]
        kept = [r for r, d in dated if d is None or start <= d <= today]
        excluded = len(dated) - len(kept)
        undated = sum(1 for _, d in dated if d is None)
    capped = max(0, len(kept) - cap)
    if capped:
        chosen = {r.id for r in sorted(kept, key=_priority)[:cap]}
        kept = [r for r in kept if r.id in chosen]
    return Selection(tuple(kept), start, excluded, undated, capped)


def _priority(review: Review) -> tuple:
    day = to_date(review.date)
    return (day is None, -(day.toordinal() if day else 0), review.rating is None, review.rating or 0)
