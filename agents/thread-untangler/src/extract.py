"""The extract calls: one pass up to SINGLE_PASS_MAX_CHARS, else one call per chunk (concurrency 4, at
most 10 chunks), each checked against the thread. Chunks still running when the map window closes are
cancelled and the reply says which messages were covered. A turn makes at most one repair in total."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import chunking
import llm
import prompts
from thread import Thread

log = logging.getLogger("thread_untangler.extract")

MAP_CONCURRENCY = 4
REVERSAL_RESERVE_SECS = 100.0  # the map window closes this long before the turn deadline


@dataclass(frozen=True)
class Mapped:
    chunks: tuple[chunking.Chunk, ...]
    parts: tuple[tuple[int, checks.Part], ...]   # (chunk index, checked part), in chunk order
    failed: int = 0
    timed_out: bool = False
    off_topic: bool = False

    @property
    def coverage(self) -> float:
        total = sum(c.size for c in self.chunks)
        return sum(self.chunks[i].size for i, _ in self.parts) / total if total else 1.0

    def notes(self) -> tuple[str, ...]:
        notes = []
        if len(self.parts) < len(self.chunks) and self.parts:
            ranges = _ranges(self.chunks, [i for i, _ in self.parts])
            notes.append(f"Covered messages {ranges} of {self.chunks[-1].last}"
                         + (" before my time limit." if self.timed_out else "."))
        if self.failed:
            notes.append(f"{self.failed} of {len(self.chunks)} parts couldn't be read (the model call failed).")
        return tuple(notes)


async def run(deps: llm.TurnDeps, thread: Thread, instruction: str, context: Sequence[str] = ()) -> Mapped:
    chunks = chunking.chunks(thread)
    budget = llm.RepairBudget(1)
    gate = asyncio.Semaphore(MAP_CONCURRENCY)
    many = len(chunks) > 1

    async def one(index: int, chunk: chunking.Chunk) -> tuple[int, str, checks.Part | None]:
        async with gate:
            part = (index + 1, len(chunks)) if many else None
            try:
                parsed = await llm.complete_json(
                    deps, prompts.extract_messages(chunk.block, instruction, context, part),
                    prompts.EXTRACT_CONTRACT, checks.parse_extract, budget)
            except llm.LlmError:
                log.warning("extract call for part %d failed", index + 1)
                return index, "failed", None
            if parsed["status"] != "ok":
                return index, parsed["status"], None
            return index, "ok", checks.check_part(parsed, thread, f"c{index + 1}.")

    tasks = [asyncio.create_task(one(i, chunk)) for i, chunk in enumerate(chunks)]
    window = deps.remaining() - REVERSAL_RESERVE_SECS if many else deps.remaining()
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, window)) if tasks else (set(), set())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results = sorted(task.result() for task in done)
    statuses = [status for _, status, _ in results]
    return Mapped(chunks=chunks, parts=tuple((i, part) for i, status, part in results if status == "ok"),
                  failed=statuses.count("failed"), timed_out=bool(pending),
                  off_topic=bool(statuses) and all(s == "off_topic" for s in statuses))


def _ranges(chunks: Sequence[chunking.Chunk], done: Sequence[int]) -> str:
    """"M1 to M142" for a run of consecutive chunks; several runs are joined with commas."""
    runs: list[list[int]] = []
    for index in done:
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return ", ".join(f"{chunks[run[0]].first} to {chunks[run[-1]].last}" for run in runs)
