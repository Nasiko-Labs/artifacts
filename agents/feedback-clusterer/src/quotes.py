"""Quotes: the model picks item ids only; code emits the item's own text, cut at a sentence boundary, so a
quote is always a real substring of what the customer wrote."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

import llm
import prompts
from items import FeedbackItem
from metrics import ThemeStats

QUOTE_MAX_CHARS = 240
MAX_QUOTES = 3
MAX_QUOTE_CALLS = 12
ITEMS_SHOWN = 40
QUOTE_RESERVE_SECS = 30.0
_SENTENCE_END = re.compile(r"[.!?](?=\s)")


class _Pick(BaseModel):
    model_config = ConfigDict(extra="ignore")
    quote_item_ids: list[str]


def _validate(data: object) -> dict | None:
    try:
        return _Pick.model_validate(data).model_dump()
    except ValidationError:
        return None


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


def choose(wanted: Sequence[str], members: Sequence[FeedbackItem]) -> list[str]:
    """Up to 3 ids from this theme, in the given order, different customers first."""
    allowed = {i.id: i for i in members}
    ordered = [i for i in dict.fromkeys(wanted) if i in allowed]
    seen: set[str] = set()
    first, rest = [], []
    for item_id in ordered:
        key = allowed[item_id].customer_key or item_id
        (rest if key in seen else first).append(item_id)
        seen.add(key)
    return (first + rest)[:MAX_QUOTES]


def fallback(members: Sequence[FeedbackItem]) -> list[str]:
    return choose([i.id for i in members], members)


async def pick(deps: llm.TurnDeps, stats: Sequence[ThemeStats], by_id: Mapping[str, FeedbackItem],
               context: Sequence[str] = ()) -> dict[str, list[str]]:
    """theme id -> quote item ids, for shown themes; a failed call falls back to a code pick."""
    shown = [s for s in stats if s.shown][:MAX_QUOTE_CALLS]

    def job(stat: ThemeStats):
        async def run() -> list[str] | None:
            members = [by_id[i] for i in stat.item_ids][:ITEMS_SHOWN]
            try:
                parsed = await llm.complete_json(deps, prompts.quote_messages({"name": stat.name}, members, context),
                                                 prompts.QUOTE_CONTRACT, _validate)
            except llm.LlmError:
                return None
            return choose(parsed["quote_item_ids"], members) or None
        return run

    results, _ = await llm.bounded([job(s) for s in shown], deps.remaining() - QUOTE_RESERVE_SECS)
    return {s.id: (ids or fallback([by_id[i] for i in s.item_ids])) for s, ids in zip(shown, results, strict=True)}


def quotes_for(stat: ThemeStats, stored_ids: Sequence[str], by_id: Mapping[str, FeedbackItem]) -> list[dict]:
    members = [by_id[i] for i in stat.item_ids if i in by_id]
    ids = choose(stored_ids, members) or fallback(members)
    return [{"text": cut_quote(by_id[i].text), "customer": by_id[i].customer or "unknown", "date": by_id[i].date or ""}
            for i in ids]
