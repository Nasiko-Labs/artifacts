"""Tagging: one call per batch of up to 30 items (at most 5, 4 at a time). The model's tags are validated in code:
unknown ids ignored, competencies limited to the list (at most 2), and `specific` re-checked by `specific.py`."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from chunking import batches, tag_block
from evidence import Evidence
from llm import LlmError, TurnDeps, complete_json
from specific import is_specific

log = logging.getLogger("performance-review-drafter.tag")

CONCURRENCY = 4
VALENCES = ("positive", "negative", "mixed", "neutral")
MAX_COMPETENCIES_PER_ITEM = 2
OK, OFF_TOPIC, FAILED = "ok", "off_topic", "failed"


@dataclass(frozen=True)
class Tagged:
    items: tuple[Evidence, ...]
    batches: int = 0
    failed_batches: int = 0
    off_topic: bool = False


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return {"status": OFF_TOPIC, "items": []}
    items = data.get("items")
    return {"status": OK, "items": items} if isinstance(items, list) else None


def apply_tags(item: Evidence, raw: dict, names: Sequence[str], person: str) -> Evidence:
    comps = [str(c).casefold().strip() for c in raw.get("competencies") or () if isinstance(c, str)]
    comps = list(dict.fromkeys(c for c in comps if c in names))[:MAX_COMPETENCIES_PER_ITEM]
    valence = str(raw.get("valence") or "").casefold()
    tagged = item.with_tags(competencies=tuple(comps), valence=valence if valence in VALENCES else "neutral",
                            tagged=True)
    specific = raw.get("specific") is True and is_specific(tagged, person)
    return tagged.with_tags(specific=specific)


async def _one(deps: TurnDeps, batch: list[Evidence], names: dict[str, str], policy: Sequence[str],
               gate: asyncio.Semaphore) -> tuple[str, dict]:
    async with gate:
        try:
            data = await complete_json(deps, prompts.tag_messages(tag_block(batch), names, policy),
                                       prompts.TAG_CONTRACT, _validate)
        except LlmError as e:
            log.warning("tag batch failed: %s", e)
            return FAILED, {}
    if data["status"] == OFF_TOPIC:
        return OFF_TOPIC, {}
    return OK, {str(r.get("id")): r for r in data["items"] if isinstance(r, dict)}


async def tag(deps: TurnDeps, items: Sequence[Evidence], names: dict[str, str], person: str,
              policy: Sequence[str] = ()) -> Tagged:
    """Tags every item the model may see (excluded items are never sent). Items of a failed batch stay
    untagged, so they are unusable and the coverage note says so."""
    todo = [i for i in items if not i.excluded and not i.tagged]
    groups = batches(todo)
    gate = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(_one(deps, g, names, policy, gate) for g in groups))
    by_id: dict[str, dict] = {}
    for status, tags in results:
        by_id.update(tags)
    failed = sum(1 for status, _ in results if status == FAILED)
    off = sum(1 for status, _ in results if status == OFF_TOPIC)
    answered = len(results) - failed
    out = tuple(apply_tags(i, by_id[i.id], list(names), person) if i.id in by_id and not i.tagged else i
                for i in items)
    return Tagged(out, len(groups), failed, off_topic=answered > 0 and off * 2 > answered)
