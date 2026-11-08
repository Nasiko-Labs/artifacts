"""Pure: the turn's evidence store. Items from every source are normalised to one shape, deduplicated by
URL, filtered to the window (HN by created date, repositories by last push; pasted items are the user's
choice and kept), capped at 40 by a pre-score, then given ids H1.. / G1.. / P1.."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

MAX_ITEMS = 40
PREFIX = {"hn": "H", "code": "G", "pasted": "P"}
SOURCE_LABEL = {"hn": "HN", "code": "the code host", "pasted": "pasted items"}


@dataclass(frozen=True)
class Item:
    source: str                 # hn | code | pasted
    title: str
    url: str = ""
    text: str = ""
    date: str = ""              # ISO date/time, "" when unknown
    points: int | None = None
    comments: int | None = None
    stars: int | None = None
    forks: int | None = None
    id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Item:
        def num(key: str) -> int | None:
            value = data.get(key)
            return None if value is None else int(value)
        return Item(source=str(data["source"]), title=str(data.get("title") or ""), url=str(data.get("url") or ""),
                    text=str(data.get("text") or ""), date=str(data.get("date") or ""), points=num("points"),
                    comments=num("comments"), stars=num("stars"), forks=num("forks"), id=str(data.get("id") or ""))


@dataclass(frozen=True)
class Store:
    items: tuple[Item, ...]
    cut: int = 0
    outside_window: int = 0
    duplicates: int = 0

    def by_id(self) -> dict[str, Item]:
        return {i.id: i for i in self.items}


def url_key(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    return f"{parts.netloc.casefold()}{parts.path.rstrip('/')}?{parts.query}"


def build(raw: Sequence[Item], window_days: int, now: datetime, age: Callable[[Item, datetime], float | None],
          prescore: Callable[[Item], float]) -> Store:
    seen: set[str] = set()
    kept: list[Item] = []
    duplicates = outside = 0
    for item in raw:
        if not item.title.strip():
            continue
        key = url_key(item.url)
        if key and key in seen:
            duplicates += 1
            continue
        days = age(item, now)
        if item.source != "pasted" and days is not None and days > window_days:
            outside += 1
            continue
        if key:
            seen.add(key)
        kept.append(item)
    ranked = sorted(range(len(kept)), key=lambda n: (-prescore(kept[n]), n))
    chosen = sorted(ranked[:MAX_ITEMS])
    cut = max(0, len(kept) - MAX_ITEMS)
    return Store(_with_ids([kept[n] for n in chosen]), cut, outside, duplicates)


def _with_ids(items: Sequence[Item]) -> tuple[Item, ...]:
    counters = {k: 0 for k in PREFIX}
    out = []
    for source in ("hn", "code", "pasted"):
        for item in (i for i in items if i.source == source):
            counters[source] += 1
            out.append(replace(item, id=f"{PREFIX[source]}{counters[source]}"))
    return tuple(out)


def compact(n: int) -> str:
    """5100 -> '5.1k'; small numbers unchanged."""
    if n < 1000:
        return str(n)
    value = math.floor(n / 100) / 10
    return f"{value:g}k"
