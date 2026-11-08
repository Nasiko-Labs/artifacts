"""Pure: every number in a brief. Signal per item, recency, theme score, corroboration bonus and strength
labels. The weights are demo defaults; the model never sees or computes them, only the resulting label and
the underlying metrics."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from store import Item

COMMENT_WEIGHT = 0.5
FORK_WEIGHT = 0.5
PUSHED_IN_WINDOW_BONUS = 0.5
PASTED_DATED_SIGNAL = 1.0
PASTED_UNDATED_SIGNAL = 0.5
CORROBORATION_BONUS = 0.25
SMALL_SET_STRONG = 10.0
SMALL_SET_MODERATE = 5.0
STRONG_MIN_SOURCES = 2
SMALL_SET_MAX_THEMES = 2


def age_days(item: Item, now: datetime) -> float | None:
    if not item.date:
        return None
    try:
        when = datetime.fromisoformat(item.date)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - when).total_seconds() / 86_400)


def item_signal(item: Item, window_days: int, now: datetime) -> float:
    if item.source == "hn":
        return math.log1p(item.points or 0) + COMMENT_WEIGHT * math.log1p(item.comments or 0)
    if item.source == "code":
        age = age_days(item, now)
        pushed = 1.0 if age is not None and age <= window_days else 0.0
        return math.log1p(item.stars or 0) + FORK_WEIGHT * math.log1p(item.forks or 0) + PUSHED_IN_WINDOW_BONUS * pushed
    return PASTED_DATED_SIGNAL if item.date else PASTED_UNDATED_SIGNAL


def recency(item: Item, window_days: int, now: datetime) -> float:
    """Half-life = half the window. Undated items are not decayed (their signal is already halved)."""
    age = age_days(item, now)
    if age is None:
        return 1.0
    return 0.5 ** (age / (window_days / 2))


def weighted(item: Item, window_days: int, now: datetime) -> float:
    return item_signal(item, window_days, now) * recency(item, window_days, now)


def theme_score(items: Sequence[Item], window_days: int, now: datetime) -> tuple[float, int]:
    """(score, distinct sources) over a theme's verified items."""
    raw = sum(weighted(i, window_days, now) for i in items)
    sources = len({i.source for i in items})
    bonus = 1 + CORROBORATION_BONUS * (max(sources, 1) - 1)
    return round(raw * bonus, 2), sources


def strength_labels(scored: Sequence[tuple[float, int]]) -> list[str]:
    """Labels for themes already sorted by score, highest first. 'strong' always needs two sources."""
    n = len(scored)
    labels = []
    for rank, (score, sources) in enumerate(scored):
        if n <= SMALL_SET_MAX_THEMES:
            label = ("strong" if score >= SMALL_SET_STRONG and sources >= STRONG_MIN_SOURCES
                     else "moderate" if score >= SMALL_SET_MODERATE else "early")
        else:
            label = "strong" if rank < n / 3 else "moderate" if rank < 2 * n / 3 else "early"
            if label == "strong" and sources < STRONG_MIN_SOURCES:
                label = "moderate"
        labels.append(label)
    return labels


def top_quartile(value: float, values: Sequence[float]) -> bool:
    """True when `value` is at or above the 75th percentile of `values`."""
    if not values:
        return False
    ordered = sorted(values)
    cut = ordered[min(len(ordered) - 1, math.floor(0.75 * len(ordered)))]
    return value >= cut
