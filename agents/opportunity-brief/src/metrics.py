"""Pure: the exact metric strings shown to the user and given to the model ("never compute"), and the
per-theme FACTS lines. Every number the model may repeat comes from here or from the evidence text."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import scoring
from store import Item, compact

CROWDED_STARS = 5_000


def age_text(item: Item, now: datetime) -> str:
    days = scoring.age_days(item, now)
    if days is None:
        return ""
    whole = int(days)
    return "today" if whole == 0 else "1 day ago" if whole == 1 else f"{whole} days ago"


def metric(item: Item, now: datetime) -> str:
    age = age_text(item, now)
    if item.source == "hn":
        return ", ".join(p for p in (f"{item.points or 0} points", f"{item.comments or 0} comments", age) if p)
    if item.source == "code":
        pushed = f"pushed {age}" if age else "push date unknown"
        return f"{compact(item.stars or 0)} stars, {compact(item.forks or 0)} forks, {pushed}"
    return f"pasted, {item.date}" if item.date else "pasted, undated"


def facts(items: Sequence[Item], now: datetime) -> list[str]:
    counts = {s: sum(1 for i in items if i.source == s) for s in ("hn", "code", "pasted")}
    lines = [f"items: {len(items)} (HN {counts['hn']}, code host {counts['code']}, pasted {counts['pasted']})"]
    hn = [i for i in items if i.source == "hn"]
    code = [i for i in items if i.source == "code"]
    if hn:
        lines.append(f"highest HN points: {max(i.points or 0 for i in hn)}")
        lines.append(f"highest HN comments: {max(i.comments or 0 for i in hn)}")
    if code:
        lines.append(f"highest repository stars: {max(i.stars or 0 for i in code)}")
        lines.append(f"repositories above 5k stars: {sum(1 for i in code if (i.stars or 0) > CROWDED_STARS)}")
    ages = [d for d in (scoring.age_days(i, now) for i in items) if d is not None]
    if ages:
        lines.append(f"newest item: {int(min(ages))} days old; oldest item: {int(max(ages))} days old")
    return lines
