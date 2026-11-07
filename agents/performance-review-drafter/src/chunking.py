"""Pure: evidence split into tag batches, and the fenced EVIDENCE blocks the model reads (one fence per item,
markers stripped, so an item cannot close its fence or speak as an instruction)."""

from __future__ import annotations

import math
from collections.abc import Sequence

import prompts
from evidence import Evidence

BATCH_ITEMS = 30
MAX_TAG_CALLS = 5
TAG_TEXT_CHARS = 1_200
DRAFT_TEXT_CHARS = 400


def batches(items: Sequence[Evidence]) -> list[list[Evidence]]:
    """30 items per batch, never more than 5 batches (150 items at most reach here)."""
    if not items:
        return []
    size = max(BATCH_ITEMS, math.ceil(len(items) / MAX_TAG_CALLS))
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def _attrs(item: Evidence, extra: str = "") -> str:
    when = f" date={item.date}" if item.date else ""
    return f"{item.id} source={item.source}{when}{extra}"


def tag_block(items: Sequence[Evidence]) -> str:
    return "\n\n".join(prompts.fence("EVIDENCE", item.text[:TAG_TEXT_CHARS], _attrs(item)) for item in items)


def draft_block(items: Sequence[Evidence]) -> str:
    blocks = []
    for item in items:
        extra = f" competencies={','.join(item.competencies)} valence={item.valence}"
        blocks.append(prompts.fence("EVIDENCE", item.text[:DRAFT_TEXT_CHARS], _attrs(item, extra)))
    return "\n\n".join(blocks)


def coverage_text(rows: dict[str, dict], gap_names: Sequence[str]) -> str:
    lines = [f"{name}: {row['usable']} usable items ({', '.join(row['ids']) or 'none'})" for name, row in rows.items()]
    if gap_names:
        lines.append("GAP competencies (write nothing about them): " + ", ".join(gap_names))
    return "\n".join(lines)
