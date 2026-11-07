"""The extraction call(s): one pass over the user's messages, or one call per chunk (concurrency 4) for
long pastes. Parts still running when the window closes are cancelled and counted; checks happen later."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import chunking
import llm
import prompts
from messages import Message
from promises import parse_extract

log = logging.getLogger("promise-extractor.extract")

CONCURRENCY = 4
STATE_RESERVE_SECS = 90.0   # the chunk window closes this long before the turn deadline


@dataclass(frozen=True)
class Extracted:
    status: str                       # ok | off_topic | failed
    candidates: tuple[dict, ...] = ()
    parts: int = 1
    failed_parts: int = 0
    timed_out_parts: int = 0


async def extract(deps: llm.TurnDeps, mine: Sequence[Message], instruction: str,
                  context: Sequence[str] = ()) -> Extracted:
    groups = chunking.chunks(mine)
    if len(groups) <= 1:
        return await _single(deps, groups[0] if groups else (), instruction, context)
    deps.budget.max_calls = llm.CHUNKED_MAX_CALLS
    return await _chunked(deps, groups, instruction, context)


async def _single(deps: llm.TurnDeps, group: Sequence[Message], instruction: str,
                  context: Sequence[str]) -> Extracted:
    try:
        parsed = await llm.complete_json(deps, prompts.extract_messages(chunking.text_of(group), instruction, context),
                                         prompts.EXTRACT_CONTRACT, parse_extract)
    except llm.LlmError:
        log.exception("extraction failed")
        return Extracted("failed", failed_parts=1)
    return Extracted(parsed["status"], tuple(parsed["promises"]))


async def _chunked(deps: llm.TurnDeps, groups: Sequence[Sequence[Message]], instruction: str,
                   context: Sequence[str]) -> Extracted:
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(index: int, group: Sequence[Message]) -> tuple[int, str, tuple[dict, ...]]:
        async with gate:
            messages = prompts.extract_messages(chunking.text_of(group), instruction, context,
                                                part=(index + 1, len(groups)))
            try:
                parsed = await llm.complete_json(deps, messages, prompts.EXTRACT_CONTRACT, parse_extract)
            except llm.LlmError:
                log.warning("extraction of part %d failed", index + 1)
                return index, "failed", ()
            return index, parsed["status"], tuple(parsed["promises"])

    tasks = [asyncio.create_task(one(i, g)) for i, g in enumerate(groups)]
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, deps.remaining() - STATE_RESERVE_SECS))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results = sorted(task.result() for task in done)
    statuses = [status for _, status, _ in results]
    candidates = tuple(c for _, status, found in results if status == "ok" for c in found)
    if statuses and all(s == "off_topic" for s in statuses) and not pending:
        return Extracted("off_topic", parts=len(groups))
    if "ok" not in statuses:
        return Extracted("failed", parts=len(groups), failed_parts=statuses.count("failed"),
                         timed_out_parts=len(pending))
    return Extracted("ok", candidates, len(groups), statuses.count("failed"), len(pending))
