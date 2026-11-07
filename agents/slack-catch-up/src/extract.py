"""Per-channel extraction: one model call per channel with messages (JSON), at most 4 at once, then
checked in code (verify.py). A channel whose call has not started by the extraction deadline is
reported as not summarized (time)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
import verify
from deps import TurnDeps
from models import Message
from steps import ask_once, parse_answer

log = logging.getLogger("catchup.extract")

CONCURRENCY = 4
MAX_PROMPT_CHARS = 12_000
_EMPTY = verify.Verified((), (), 0)


@dataclass(frozen=True)
class Extracted:
    channel_id: str
    verified: verify.Verified
    summarized: bool = True
    note: str = ""


def lines_for(messages: Sequence[Message]) -> tuple[str, bool]:
    """`[ts] author: text` lines, oldest first, human messages only; the latest that fit the cap."""
    rows = [f"[{m.ts}] {m.author}: {' '.join(m.text.split())}" for m in messages if m.is_human]
    kept: list[str] = []
    size = 0
    for row in reversed(rows):
        if size + len(row) + 1 > MAX_PROMPT_CHARS:
            break
        kept.append(row)
        size += len(row) + 1
    return "\n".join(reversed(kept)), len(kept) < len(rows)


async def extract_all(deps: TurnDeps, work: Sequence[tuple[str, str, Sequence[Message]]]) -> dict[str, Extracted]:
    """`work` = (channel id, label, messages) in rank order."""
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(cid: str, label: str, messages: Sequence[Message]) -> Extracted:
        async with gate:
            if deps.past_extract_deadline():
                return Extracted(cid, _EMPTY, summarized=False, note="not summarized (time)")
            return await _extract(deps, cid, label, messages)

    results = await asyncio.gather(*(one(c, label, m) for c, label, m in work))
    return {r.channel_id: r for r in results}


async def _extract(deps: TurnDeps, cid: str, label: str, messages: Sequence[Message]) -> Extracted:
    lines, truncated = lines_for(messages)
    if not lines:
        return Extracted(cid, _EMPTY)
    request = ({"role": "system", "content": prompts.EXTRACT_SYSTEM},
               {"role": "user", "content": prompts.extract_request(label, lines, truncated)})
    answer = parse_answer(await ask_once(deps, request))
    if answer is None:
        log.info("extraction for %s was not JSON; showing counts and mentions only", label)
        return Extracted(cid, _EMPTY, note="highlights unavailable (unreadable answer)")
    return Extracted(cid, verify.check(answer, messages))
