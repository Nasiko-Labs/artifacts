"""Quotes: the model picks item ids only (one call per shown theme, at most 12, 4 at a time); code emits the
item's own text cut at a sentence boundary, so a quote is always a real substring of what the customer wrote.
An unknown id is dropped; a failed call falls back to a code pick."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import prompts
import records
from deps import TurnDeps
from pool import bounded
from reader import ask_once
from records import Item, RawPick
from themes import ThemeStats, customer_key

QUOTE_MAX_CHARS = 240
MAX_QUOTES = 3
ITEMS_SHOWN = 40
_SENTENCE_END = re.compile(r"[.!?](?=\s)")


def cut_quote(text: str) -> str:
    """<= 240 chars: the last whole sentence that fits, else the last whole word plus an ellipsis."""
    clean = " ".join((text or "").split())
    if len(clean) <= QUOTE_MAX_CHARS:
        return clean
    head = clean[:QUOTE_MAX_CHARS]
    ends = [m.end() for m in _SENTENCE_END.finditer(head + " ")]
    if ends and ends[-1] >= 40:
        return head[:ends[-1]]
    return head[:head.rfind(" ")].rstrip(" ,;:") + " ..." if " " in head else head


def choose(wanted: Sequence[str], members: Sequence[Item]) -> list[str]:
    """Up to 3 ids from this theme, in the given order, different customers first."""
    allowed = {i.id: i for i in members}
    ordered = [i for i in dict.fromkeys(wanted) if i in allowed]
    seen: set[str] = set()
    first, rest = [], []
    for item_id in ordered:
        key = customer_key(allowed[item_id].customer) or item_id
        (rest if key in seen else first).append(item_id)
        seen.add(key)
    return (first + rest)[:MAX_QUOTES]


def fallback(members: Sequence[Item]) -> list[str]:
    return choose([i.id for i in members], members)


async def pick(deps: TurnDeps, stats: Sequence[ThemeStats], by_id: Mapping[str, Item]) -> tuple[dict[str, list[str]], bool]:
    """theme id -> quote item ids for shown themes, and whether the turn limit cut the step."""
    shown = [s for s in stats if s.shown][:deps.config.max_quote_themes]

    def job(stat: ThemeStats):
        async def run() -> list[str] | None:
            members = [by_id[i] for i in stat.item_ids][:ITEMS_SHOWN]
            parsed = records.parse(RawPick, await ask_once(deps, prompts.quote_messages(stat.name, members)))
            return choose(parsed.quote_item_ids, members) if parsed else None
        return run

    ran = await bounded([job(s) for s in shown], deps.config.concurrency)
    picked = {s.id: (ids or fallback([by_id[i] for i in s.item_ids])) for s, ids in zip(shown, ran.results, strict=True)}
    return picked, ran.cut


def quotes_for(stat: ThemeStats, ids: Sequence[str], by_id: Mapping[str, Item]) -> list[dict]:
    members = [by_id[i] for i in stat.item_ids if i in by_id]
    chosen = choose(ids, members) or fallback(members)
    return [{"text": cut_quote(by_id[i].text), "customer": by_id[i].customer or "unknown", "date": by_id[i].date,
             "url": by_id[i].url} for i in chosen]
