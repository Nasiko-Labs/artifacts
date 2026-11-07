"""Pure: how items are split across model calls. Fixed sizes, so a 2,000-item paste stays within the
call budget: at most 10 discovery calls (400 items) and 50 assignment calls."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from items import FeedbackItem

BATCH_SIZE = 40
DISCOVERY_POOL_MAX = 400
MAX_DISCOVERY_CALLS = 10
MAX_ASSIGN_CALLS = 50
MODEL_ITEM_CHARS = 1_200

T = TypeVar("T")


def batches(rows: Sequence[T], size: int = BATCH_SIZE) -> list[list[T]]:
    return [list(rows[i:i + size]) for i in range(0, len(rows), size)]


def discovery_pool(items: Sequence[FeedbackItem]) -> list[FeedbackItem]:
    """All items when there are at most 400; else the first 400 by ARR (desc), then date (desc)."""
    if len(items) <= DISCOVERY_POOL_MAX:
        return list(items)
    newest_first = sorted(items, key=lambda i: i.date, reverse=True)  # ISO dates; undated ("") last
    return sorted(newest_first, key=lambda i: -(i.arr or 0.0))[:DISCOVERY_POOL_MAX]


def for_model(text: str) -> str:
    """Each item is cut for the model; the full text stays for quotes."""
    return text if len(text) <= MODEL_ITEM_CHARS else text[:MODEL_ITEM_CHARS].rstrip() + " ..."

