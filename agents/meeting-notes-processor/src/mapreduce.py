"""Map-reduce for long meetings.

Map: one call per chunk (concurrency MAP_CONCURRENCY); each answer is checked against the parsed turns
and its items get ids like c3.a2. Parts still running when the map window closes are cancelled and the
reply says how much was covered. Reduce: one call that may only select, merge and order ids and name the
merged topics; every quote comes from a chosen id, so the reduce step cannot add an item. If it fails,
the union of the checked map items is shown instead."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import chunking
import llm
import prompts
from items import Action, Bullet, Decision, Result, Topic, Turn

log = logging.getLogger("meeting-notes.mapreduce")

MAP_CONCURRENCY = 4
REDUCE_RESERVE_SECS = 100.0  # the map window closes this long before the turn deadline
FALLBACK_NOTE = "I couldn't merge the parts, so the notes are listed part by part."


@dataclass(frozen=True)
class Part:
    index: int
    topics: tuple[Topic, ...]
    decisions: tuple[Decision, ...]
    actions: tuple[Action, ...]
    dropped: int


@dataclass(frozen=True)
class Mapped:
    chunks: tuple[tuple[Turn, ...], ...]
    parts: tuple[Part, ...]
    timed_out: bool = False
    failed: int = 0
    off_topic: str = ""   # the shared non-ok status when every part returned one

    @property
    def dropped(self) -> int:
        return sum(p.dropped for p in self.parts)

    @property
    def coverage(self) -> float:
        sizes = [len(chunking.as_text(c)) for c in self.chunks]
        return sum(sizes[p.index] for p in self.parts) / max(1, sum(sizes))

    def notes(self) -> tuple[str, ...]:
        notes = []
        if self.timed_out:
            notes.append(coverage_note(self))
        if self.failed:
            notes.append(f"{self.failed} of {len(self.chunks)} parts couldn't be processed (the model call failed).")
        return tuple(notes)


def coverage_note(mapped: Mapped) -> str:
    done = {p.index for p in mapped.parts}
    prefix = 0
    while prefix in done:
        prefix += 1
    turns = [t for c in mapped.chunks for t in c]
    covered = [t for c in mapped.chunks[:prefix] for t in c]
    if covered and covered[0].time and covered[-1].time and turns[-1].time:
        return f"Covered {covered[0].time} to {covered[-1].time} of {turns[-1].time} before my time limit."
    share = round(100 * mapped.coverage)
    return f"Covered {len(done)} of {len(mapped.chunks)} parts (about {share}%) before my time limit."


async def map_chunks(deps: llm.TurnDeps, chunks: Sequence[tuple[Turn, ...]], index: checks.Index, instruction: str,
                     context: Sequence[str] = ()) -> Mapped:
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(i: int, chunk: tuple[Turn, ...]) -> tuple[int, str, Part | None]:
        async with gate:
            messages = prompts.notes_messages(chunking.as_text(chunk), instruction, context, part=(i + 1, len(chunks)))
            try:
                parsed = await llm.complete_json(deps, messages, prompts.NOTES_CONTRACT, checks.parse_notes)
            except llm.LlmError:
                log.warning("map call for part %d failed", i + 1)
                return i, "failed", None
            if parsed["status"] != "ok":
                return i, parsed["status"], None
            topics, decisions, actions, dropped = checks.check_all(parsed, index, prefix=f"c{i + 1}.")
            return i, "ok", Part(i, tuple(topics), tuple(decisions), tuple(actions), dropped)

    tasks = [asyncio.create_task(one(i, c)) for i, c in enumerate(chunks)]
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, deps.remaining() - REDUCE_RESERVE_SECS))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results = sorted((t.result() for t in done), key=lambda r: r[0])
    statuses = {status for _, status, _ in results}
    shared = next(iter(statuses)) if len(statuses) == 1 and statuses & {"off_topic", "not_meeting"} else ""
    return Mapped(tuple(chunks), tuple(p for _, s, p in results if p is not None), bool(pending),
                  sum(1 for _, s, _ in results if s == "failed"), shared)


def items_block(mapped: Mapped) -> str:
    lines = []
    for part in mapped.parts:
        for topic in part.topics:
            lines.append(f"{topic.item_id} [topic] {topic.title}")
            lines += [f"  {b.item_id} {b.text}" for b in topic.bullets]
        lines += [f"{d.item_id} [decision] {d.text}" for d in part.decisions]
        lines += [f"{a.item_id} [{a.kind}] {a.task} (owner: {a.owner}; due: {a.due or 'not stated'})"
                  for a in part.actions]
    return "\n".join(lines)


def parse_reduce(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("topics"), list):
        return None
    topics, decisions, actions = data["topics"], data.get("decisions") or [], data.get("actions") or []
    if not all(isinstance(x, list) for x in (decisions, actions)) or not all(isinstance(t, Mapping) for t in topics):
        return None
    return {"topics": [dict(t) for t in topics], "decisions": [str(x) for x in decisions],
            "actions": [str(x) for x in actions]}


async def reduce(deps: llm.TurnDeps, mapped: Mapped, index: checks.Index, instruction: str,
                 context: Sequence[str] = ()) -> Result:
    messages = prompts.reduce_messages(items_block(mapped), instruction, len(mapped.chunks), context)
    try:
        parsed = await llm.complete_json(deps, messages, prompts.REDUCE_CONTRACT, parse_reduce, llm.REDUCE_MAX_TOKENS)
    except llm.LlmError:
        log.exception("reduce failed; showing the union of the checked map items")
        return union(mapped, (FALLBACK_NOTE,))
    return apply_reduce(parsed, mapped, index)


def apply_reduce(parsed: Mapping[str, Any], mapped: Mapped, index: checks.Index) -> Result:
    bullets = {b.item_id: (b, t) for p in mapped.parts for t in p.topics for b in t.bullets}
    decisions = {d.item_id: d for p in mapped.parts for d in p.decisions}
    actions = {a.item_id: a for p in mapped.parts for a in p.actions}
    topics = []
    for n, entry in enumerate(parsed["topics"], start=1):
        ids = entry.get("bullet_ids") if isinstance(entry.get("bullet_ids"), list) else []
        chosen = [bullets[str(i)] for i in ids if str(i) in bullets]
        if not chosen:
            continue
        title = checks.grounded_text(entry.get("title"), checks.WORD_CAPS["title"], index) or chosen[0][1].title
        unique: list[Bullet] = checks.dedup((b for b, _ in chosen), lambda b: b.text)
        topics.append(Topic(title, tuple(unique[:checks.CAPS["bullets"]]), f"r.t{n}"))
    if not topics:
        topics = [t for p in mapped.parts for t in p.topics]
    picked_d = [decisions[i] for i in parsed["decisions"] if i in decisions]
    picked_a = [actions[i] for i in parsed["actions"] if i in actions]
    return _result(mapped, topics, picked_d, picked_a, mapped.notes())


def union(mapped: Mapped, extra: tuple[str, ...] = ()) -> Result:
    return _result(mapped, [t for p in mapped.parts for t in p.topics], [d for p in mapped.parts for d in p.decisions],
                   [a for p in mapped.parts for a in p.actions], (*mapped.notes(), *extra))


def _result(mapped: Mapped, topics, decisions, actions, notes: tuple[str, ...]) -> Result:
    kept = sum(len(t.bullets) for p in mapped.parts for t in p.topics) + sum(
        len(p.decisions) + len(p.actions) for p in mapped.parts)
    t, d, a, capped = checks.finalize(topics, decisions, actions)
    return Result(t, d, a, kept_before_caps=kept, dropped=mapped.dropped, capped=capped, failed_parts=mapped.failed,
                  total_parts=len(mapped.chunks), coverage=mapped.coverage, notes=notes)
