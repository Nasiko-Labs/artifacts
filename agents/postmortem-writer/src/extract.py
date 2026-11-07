"""Extract (map): one call per chunk, at most MAP_CONCURRENCY at once, each chunk's items checked against
THAT chunk. Parts still running when the map window closes (MAP_RESERVE_SECS before the turn deadline) are
cancelled, and the reply says how much was covered and which part failed."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import checks
import chunking
import llm
import prompts
from merge import Merged, merge
from times import Line

log = logging.getLogger("postmortem.extract")

MAP_CONCURRENCY = 4
MAP_RESERVE_SECS = 100.0
SINGLE_PASS_REPAIRS = 1
LONG_LOG_REPAIRS = 2


class RepairQuota:
    """How many JSON repairs the map may spend, so the write call always keeps its own budget."""

    def __init__(self, allowed: int) -> None:
        self.left = allowed

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


@dataclass(frozen=True)
class Extracted:
    status: str                  # ok | off_topic | not_incident | failed
    merged: Merged | None = None
    total: int = 1
    covered: int = 0
    failed: tuple[int, ...] = ()
    timed_out: bool = False

    def notes(self) -> tuple[str, ...]:
        notes = []
        if self.timed_out:
            notes.append(f"Covered {self.covered} of {self.total} parts of the log before my time limit; "
                         "the rest is not in this draft.")
        if self.failed:
            named = ", ".join(str(n) for n in self.failed)
            notes.append(f"Part {named} of {self.total} couldn't be read (the model call failed); "
                         "events from it are missing.")
        return tuple(notes)

    @property
    def partial(self) -> bool:
        return self.timed_out or bool(self.failed)


def check_chunk(parsed: dict, view: checks.View, speakers: Sequence[str], prefix: str) -> checks.Checked:
    """Events first (factors link to them), then factors (actions link to them), then actions."""
    dropped = 0
    events, event_ids = [], {}
    for n, entry in enumerate(parsed["events"], start=1):
        event = checks.check_event(entry, view, f"{prefix}e{n}")
        dropped += event is None
        if event is not None:
            events.append(event)
            event_ids[str(entry.get("id") or f"e{n}")] = event.id
    factors, factor_ids = [], {}
    for n, entry in enumerate(parsed["factors"], start=1):
        factor = checks.check_factor(entry, view, f"{prefix}f{n}", event_ids)
        dropped += factor is None
        if factor is not None:
            factors.append(factor)
            factor_ids[str(entry.get("id") or f"f{n}")] = factor.id
    actions = []
    for n, entry in enumerate(parsed["actions"], start=1):
        action = checks.check_action(entry, view, f"{prefix}a{n}", speakers, factor_ids)
        dropped += action is None
        actions += [action] if action is not None else []
    return checks.Checked(tuple(events), tuple(factors), tuple(actions), dropped)


async def extract(deps: llm.TurnDeps, lines: Sequence[Line], instruction: str, speakers: Sequence[str],
                  context: Sequence[str] = ()) -> Extracted:
    chunks = chunking.split(lines)
    gate = asyncio.Semaphore(MAP_CONCURRENCY)
    total = len(chunks)
    quota = RepairQuota(SINGLE_PASS_REPAIRS if total == 1 else LONG_LOG_REPAIRS)
    deps.budget.plan(total, quota.left)

    async def one(index: int, chunk: tuple[Line, ...]) -> tuple[int, str, checks.Checked | None]:
        view = checks.view_of(chunk)
        part = (index + 1, total) if total > 1 else None
        async with gate:
            try:
                parsed = await llm.complete_json(
                    deps, prompts.extract_messages(view.text, instruction, context, part), prompts.EXTRACT_CONTRACT,
                    checks.parse_extract, llm.MAP_MAX_TOKENS, repair=quota.take)
            except llm.LlmError:
                log.warning("extract call for part %d failed", index + 1)
                return index, "failed", None
        if parsed["status"] != "ok":
            return index, parsed["status"], None
        return index, "ok", check_chunk(parsed, view, speakers, f"c{index + 1}.")

    tasks = [asyncio.create_task(one(i, chunk)) for i, chunk in enumerate(chunks)]
    window = deps.remaining() - (MAP_RESERVE_SECS if total > 1 else 0.0)
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, window))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results = sorted(task.result() for task in done)
    ok = [checked for _, status, checked in results if status == "ok"]
    statuses = [status for _, status, _ in results]
    failed = tuple(i + 1 for i, status, _ in results if status == "failed")
    if not ok:
        for status in ("off_topic", "not_incident"):
            if statuses and all(s == status for s in statuses):
                return Extracted(status, total=total)
        return Extracted("failed", total=total, failed=failed, timed_out=bool(pending))
    return Extracted("ok", merge(ok), total, len(ok), failed, bool(pending))
