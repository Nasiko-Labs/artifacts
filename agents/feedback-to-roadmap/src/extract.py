"""The extract step: one tool-less call per chunk of 12 items (at most 14 chunks, 4 at a time). Each item gets
a short label naming its underlying need; a question with no problem stated, a missing or malformed label, or
an unknown id leaves the item unlabelled (`none`). The model returns no quote, count, customer or number."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

import prompts
import records
from deps import TurnDeps
from pool import bounded
from reader import ask_once
from records import Item, RawLabels

MIN_LABEL_WORDS, MAX_LABEL_WORDS = 2, 8
_NOT_WORD = re.compile(r"[^\w\s/&+-]+")


@dataclass(frozen=True)
class Extracted:
    items: tuple[Item, ...]        # every item, `label` set or ""
    unreached: int = 0             # items whose chunk was over the cap, failed or was cut
    cut: bool = False
    calls: int = 0


def chunks(items: Sequence[Item], size: int) -> list[list[Item]]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def clean_label(raw: str) -> str:
    words = _NOT_WORD.sub(" ", prompts.strip_markers(raw or "")).split()
    if len(words) < MIN_LABEL_WORDS:
        return ""
    return " ".join(words[:MAX_LABEL_WORDS]).casefold()


async def extract_step(deps: TurnDeps, items: Sequence[Item]) -> Extracted:
    cfg = deps.config
    groups = chunks(items, cfg.extract_chunk)
    asked = groups[:cfg.max_extract_chunks]

    def job(group: list[Item]):
        async def run() -> RawLabels | None:
            return records.parse(RawLabels, await ask_once(deps, prompts.extract_messages(group)))
        return run

    ran = await bounded([job(g) for g in asked], cfg.concurrency)
    labels: dict[str, str] = {}
    unreached = sum(len(g) for g in groups[cfg.max_extract_chunks:])
    for group, parsed in zip(asked, ran.results, strict=True):
        if parsed is None:
            unreached += len(group)
            continue
        ids = {i.id for i in group}
        for entry in parsed.labels:
            if entry.item_id in ids and entry.item_id not in labels and not entry.is_question:
                labels[entry.item_id] = clean_label(entry.label)
    labelled = tuple(replace(i, label=labels.get(i.id, "")) for i in items)
    return Extracted(labelled, unreached, ran.cut, len(asked))
