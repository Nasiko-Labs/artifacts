"""Pure helpers for batched model calls: fixed-size chunks run with bounded concurrency."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from llm import CONCURRENCY

T = TypeVar("T")
R = TypeVar("R")


def chunks(items: Sequence[T], size: int) -> list[list[T]]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


async def gather_bounded(batches: Sequence[T], run: Callable[[T], Awaitable[R]], limit: int = CONCURRENCY) -> list[R]:
    gate = asyncio.Semaphore(limit)

    async def one(batch: T) -> R:
        async with gate:
            return await run(batch)

    return list(await asyncio.gather(*(one(b) for b in batches)))
