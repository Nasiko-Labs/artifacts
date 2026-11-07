"""The extract step: one tool-less LLM call per chunk (fenced transcript), a few at a time, under the
turn's deadline. Calls are scheduled whole: a call any of whose chunks could not start in time is
reported "not analysed (time)", its items discarded, and offered again via "continue"."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import checks
import prompts
import transcript
from deps import TurnDeps
from reader import ask_once
from records import Call, Item, Turn, load_json_object

log = logging.getLogger("call_miner.extract")


@dataclass(frozen=True)
class Extracted:
    items: tuple[Item, ...]
    done: tuple[str, ...]            # call ids fully analysed
    timed_out: tuple[str, ...]       # call ids not analysed for lack of time
    partly: dict[str, tuple[int, int]]  # call id → (parts analysed, parts in total)
    dropped: Counter


async def extract(deps: TurnDeps, calls: Sequence[Call], turns: Mapping[str, Sequence[Turn]],
                  matches: Mapping[str, str | None]) -> Extracted:
    cfg = deps.config
    gate = asyncio.Semaphore(cfg.extract_concurrency)
    plans = {c.id: transcript.chunk(turns[c.id], cfg.chunk_chars, cfg.max_chunks_per_call)
             for c in calls if c.id in turns}

    async def run(call: Call, text: str) -> tuple[str, list | None]:
        async with gate:
            if deps.elapsed() >= cfg.extract_stop_secs:
                return call.id, None
            return call.id, await _one(deps, text)

    jobs = [run(c, text) for c in calls if c.id in plans for text in plans[c.id].chunks]
    results = await asyncio.gather(*jobs)
    late = {cid for cid, raw in results if raw is None}
    by_call: dict[str, list] = {}
    for cid, raw in results:
        if raw is not None and cid not in late:
            by_call.setdefault(cid, []).append(raw)
    items: list[Item] = []
    dropped: Counter = Counter()
    for call in calls:
        for raw in by_call.get(call.id, ()):
            check = checks.checked_items(raw, call, turns[call.id], matches.get(call.id), cfg)
            items.extend(i for i in check.items if i not in items)
            dropped.update(check.dropped)
    done = tuple(c.id for c in calls if c.id in plans and c.id not in late)
    partly = {cid: (len(p.chunks), p.total) for cid, p in plans.items() if p.total > len(p.chunks) and cid in done}
    if late:
        log.info("deadline: %d call(s) not analysed", len(late))
    return Extracted(tuple(items), done, tuple(c.id for c in calls if c.id in late), partly, dropped)


async def _one(deps: TurnDeps, text: str) -> list:
    messages = ({"role": "system", "content": prompts.EXTRACT_SYSTEM},
                {"role": "user", "content": prompts.fence_transcript(text)})
    try:
        data = load_json_object(await ask_once(deps, messages))
    except Exception as e:  # noqa: BLE001 - one failed chunk must not end the turn
        log.warning("extraction call failed: %s", type(e).__name__)
        return []
    raw = data.get("items") if data else None
    return raw if isinstance(raw, list) else []
