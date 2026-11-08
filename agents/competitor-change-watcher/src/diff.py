"""Pure: what changed in one block. Changelog: entries whose fingerprint is absent from the baseline (the
`Before:` text, else the last paste in this conversation); with no baseline, entries dated within the last
`recent_days` count as new and undated entries are listed once as "no baseline". Pricing: price facts
between the baseline and the current text."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

import entries as entry_split
import pricing
import snapshot
from records import Block, Entry, PriceFact
from words import fingerprint


@dataclass(frozen=True)
class BlockDiff:
    block: Block
    baseline: str                        # before | snapshot | none
    total: int                           # entries (or plans) in the current text
    new: tuple[Entry, ...] = ()
    unbaselined: tuple[Entry, ...] = ()  # undated entries with no baseline
    older: int = 0                       # dated entries older than the window, with no baseline
    price_facts: tuple[PriceFact, ...] = ()
    pricing_failed: bool = False         # a pricing block where no plan could be read


def _changelog(block: Block, base: str | None, kind: str, today: date, days: int, start: int) -> BlockDiff:
    current = entry_split.entries(block.body, block.competitor, block.source, start)
    if base is not None:
        seen = {fingerprint(t) for t, _ in entry_split.split(base)}
        new = tuple(e for e in current if fingerprint(e.text) not in seen)
        return BlockDiff(block, kind, len(current), new=new)
    since = (today - timedelta(days=days)).isoformat()
    dated = [e for e in current if e.date]
    return BlockDiff(block, kind, len(current), new=tuple(e for e in dated if e.date >= since),
                     unbaselined=tuple(e for e in current if not e.date),
                     older=sum(1 for e in dated if e.date < since))


def _pricing(block: Block, base: str | None, kind: str) -> BlockDiff:
    current = pricing.plans(block.body)
    facts = tuple(pricing.diff(base, block.body)) if base is not None and current else ()
    return BlockDiff(block, kind, len(current), price_facts=facts, pricing_failed=not current)


def diff_block(block: Block, snapshots: Mapping[str, str], today: date, days: int, start: int) -> BlockDiff:
    base, kind = snapshot.baseline(snapshots, block)
    if block.is_pricing:
        return _pricing(block, base, kind)
    return _changelog(block, base, kind, today, days, start)


def diff_all(blocks: tuple[Block, ...], snapshots: Mapping[str, str], today: date, days: int) -> list[BlockDiff]:
    """Blocks are compared in paste order; a later block for the same competitor and source compares with the
    earlier one in the same message."""
    out: list[BlockDiff] = []
    stored = dict(snapshots)
    start = 1
    for block in blocks:
        result = diff_block(block, stored, today, days, start)
        out.append(result)
        start += result.total
        stored = snapshot.replaced(stored, (block,))
    return out
