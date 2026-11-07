"""Fact extraction: one call for a thread up to SINGLE_PASS_MAX_CHARS, else one call per chunk
(concurrency MAP_CONCURRENCY, at most MAX_CHUNKS). Each answer is checked against the whole thread
(facts.verify) and the parts are merged in code. The map window closes MAP_RESERVE_SECS before the turn
deadline; parts still running are cancelled and the reply says how much was covered."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import chunking
import facts as fx
import llm
import prompts
from messages import Message

log = logging.getLogger("escalation.extract")

MAP_CONCURRENCY = 4
MAP_RESERVE_SECS = 90.0


@dataclass(frozen=True)
class Extracted:
    status: str                     # ok | off_topic | not_thread | failed
    facts: tuple[fx.Fact, ...] = ()
    dropped: int = 0
    parts: int = 1
    covered: int = 1
    failed: int = 0

    @property
    def partial(self) -> bool:
        return self.covered < self.parts

    def notes(self) -> tuple[str, ...]:
        notes = []
        if self.partial:
            notes.append(f"Covered {self.covered} of {self.parts} parts of the thread before my time limit.")
        if self.failed:
            notes.append(f"{self.failed} of {self.parts} parts couldn't be read (the model call failed).")
        return tuple(notes)


async def extract(deps: llm.TurnDeps, thread: str, messages: tuple[Message, ...], labelled: str,
                  context: Sequence[str] = ()) -> Extracted:
    if len(labelled) <= chunking.SINGLE_PASS_MAX_CHARS:
        return await _single(deps, thread, messages, labelled, context)
    return await _map(deps, thread, messages, chunking.split(labelled), context)


async def _single(deps, thread, messages, labelled, context) -> Extracted:
    try:
        parsed = await llm.complete_json(deps, prompts.extract_messages(labelled, "", context),
                                         prompts.EXTRACT_CONTRACT, fx.parse_extract, llm.EXTRACT_MAX_TOKENS)
    except llm.LlmError:
        log.exception("fact extraction failed")
        return Extracted("failed")
    if parsed["status"] != "ok":
        return Extracted(parsed["status"])
    checked = fx.verify(parsed["facts"], thread, messages)
    return Extracted("ok", fx.merge([checked.facts]), checked.dropped)


async def _map(deps, thread, messages, chunks: Sequence[str], context) -> Extracted:
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(index: int, chunk: str) -> tuple[int, str, fx.Verified | None]:
        async with gate:
            try:
                parsed = await llm.complete_json(
                    deps, prompts.extract_messages(chunk, "", context, part=(index + 1, len(chunks))),
                    prompts.EXTRACT_CONTRACT, fx.parse_extract, llm.EXTRACT_MAX_TOKENS)
            except llm.LlmError:
                log.warning("extraction of part %d failed", index + 1)
                return index, "failed", None
            if parsed["status"] != "ok":
                return index, parsed["status"], None
            return index, "ok", fx.verify(parsed["facts"], thread, messages)

    tasks = [asyncio.create_task(one(i, chunk)) for i, chunk in enumerate(chunks)]
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, deps.remaining() - MAP_RESERVE_SECS))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results = sorted(task.result() for task in done)
    statuses = [status for _, status, _ in results]
    groups = [fx.prefixed(v.facts, f"c{i + 1}.") for i, status, v in results if status == "ok" and v]
    dropped = sum(v.dropped for _, status, v in results if status == "ok" and v)
    covered = len(results)
    if not groups:
        off = bool(statuses) and all(s in ("off_topic", "not_thread") for s in statuses)
        return Extracted(statuses[0] if off else "failed", parts=len(chunks), covered=covered)
    return Extracted("ok", fx.merge(groups), dropped, len(chunks), covered, statuses.count("failed"))
