"""Large inputs: the map step. Sections are packed into at most MAX_GROUPS groups of about
GROUP_CHARS; one model call per group (MAP_CONCURRENCY at a time) proposes candidate risks,
decisions and key facts, each with section + snippet. Candidates are checked in code at once
(cites grounded, wording grounded, decisions open) and numbered R#, D#, F#. Groups still running
when the map window closes are cancelled; the brief is then written from the finished ones."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import brief as b
import prompts
from checks import is_open
from grounding import Sources
from llm import CALL_TIMEOUT_SECS, Llm
from render import clean
from sections import Section

log = logging.getLogger("exec_brief.map")

GROUP_CHARS = 12_000
MAX_GROUPS = 6
MAP_CONCURRENCY = 4
COMPOSE_RESERVE_SECS = 2 * CALL_TIMEOUT_SECS  # compose + its repair
_KINDS = (("risks", "R", "risk"), ("decisions", "D", "decision"), ("key_facts", "F", "fact"))


@dataclass(frozen=True)
class MapResult:
    candidates: tuple[dict, ...]
    done: int
    total: int


def groups(sections: Sequence[Section]) -> list[list[Section]]:
    """Sections in order, cut into consecutive groups of at most `size` chars (a section may span two
    groups; its cites are still checked against the whole section). At most MAX_GROUPS groups."""
    total = sum(len(s.text) for s in sections)
    size = max(GROUP_CHARS, math.ceil(total / MAX_GROUPS))
    packed: list[list[Section]] = [[]]
    used = 0
    for section in sections:
        start = 0
        while start < len(section.text):
            if used >= size:
                packed.append([])
                used = 0
            take = section.text[start:start + size - used]
            packed[-1].append(replace(section, text=take))
            used += len(take)
            start += len(take)
    return [group for group in packed if group]


async def run(llm: Llm, sections: Sequence[Section]) -> MapResult:
    batches = groups(sections)
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(batch: list[Section]) -> dict | None:
        async with gate:
            messages = ({"role": "system", "content": prompts.MAP_SYSTEM},
                        {"role": "user", "content": prompts.map_user(batch)})
            return await llm.json(messages)

    tasks = [asyncio.create_task(one(batch)) for batch in batches]
    window = max(0.0, llm.remaining() - COMPOSE_RESERVE_SECS)
    finished, pending = await asyncio.wait(tasks, timeout=window)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    answers = [task.result() for task in tasks if task in finished and _succeeded(task)]
    candidates = _validated(answers, Sources(sections))
    return MapResult(candidates, len(answers), len(batches))


def _succeeded(task: asyncio.Task) -> bool:
    if task.cancelled():
        return False
    error = task.exception()
    if error is not None:
        log.warning("a map call failed: %s", error)
        return False
    return task.result() is not None


def _validated(answers: Sequence[dict], sources: Sources) -> tuple[dict, ...]:
    out: list[dict] = []
    counters = {prefix: 0 for _, prefix, _ in _KINDS}
    for answer in answers:
        for key, prefix, kind in _KINDS:
            for line in b.parse_lines(answer.get(key), kind):
                candidate = _candidate(line, sources, kind)
                if candidate is None:
                    continue
                counters[prefix] += 1
                out.append({"id": f"{prefix}{counters[prefix]}", "kind": kind, **candidate})
    return tuple(out)


def _candidate(line: b.Line, sources: Sources, kind: str) -> dict | None:
    text = clean(line.text)
    cites = sources.ground(line.cites)
    if not cites or sources.ungrounded(text, cites):
        return None
    if kind == "decision" and not is_open(replace(line, cites=cites)):
        return None
    row = {"text": text, "cites": [{"section": c.section, "snippet": c.snippet} for c in cites]}
    if kind == "risk":
        row["severity"] = line.severity
    if kind == "decision":
        row.update(owner=line.owner, by=line.by)
    return row
