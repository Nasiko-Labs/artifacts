"""Run a list of model-call jobs with bounded concurrency. A job cut by the turn limit, or one that fails,
yields None; `cut` says the turn limit was hit, so the caller can label the result as partial."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import openai

from budget import TurnLimitReached

log = logging.getLogger("feedback_to_roadmap.pool")


@dataclass(frozen=True)
class Ran:
    results: tuple[Any, ...]
    cut: bool = False


async def bounded(jobs: Sequence[Callable[[], Awaitable[Any]]], limit: int) -> Ran:
    gate = asyncio.Semaphore(max(1, limit))
    cut = False

    async def one(job: Callable[[], Awaitable[Any]]) -> Any:
        nonlocal cut
        async with gate:
            if cut:
                return None
            try:
                return await job()
            except TurnLimitReached:
                cut = True
                return None
            except openai.OpenAIError as e:
                log.warning("model call failed: %s", type(e).__name__)
                return None

    results = await asyncio.gather(*(one(j) for j in jobs))
    return Ran(tuple(results), cut)
