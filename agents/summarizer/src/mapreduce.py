"""Map-reduce for long sources.

Map: one call per chunk (concurrency MAP_CONCURRENCY), each chunk's items checked against THAT chunk
and given ids like c3.a2. Parts still running when the map window closes are cancelled and the reply
says how much was covered. Reduce: one call that may only select, merge and order ids and write the
TL;DR; snippets come from the chosen ids, so the reduce step cannot introduce a quote. If it fails, the
union of the checked map items is shown instead."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import grounding as g
import llm
import prompts
from items import NOT_STATED, Item, Summary

log = logging.getLogger("summarizer.mapreduce")

MAP_CONCURRENCY = 4
REDUCE_RESERVE_SECS = 100.0  # the map window closes this long before the turn deadline
FALLBACK_NOTE = "I couldn't merge the parts, so this lists the checked items part by part."


@dataclass(frozen=True)
class Mapped:
    total: int
    parts: tuple[tuple[int, tuple[checks.Checked, ...]], ...]  # (chunk index, (tldr, decisions, actions))
    sizes: tuple[int, ...]
    timed_out: bool = False
    failed: int = 0
    off_topic: bool = False

    @property
    def pool(self) -> tuple[Item, ...]:
        return tuple(item for _, sections in self.parts for checked in sections for item in checked.items)

    @property
    def dropped(self) -> int:
        return sum(checked.dropped for _, sections in self.parts for checked in sections)

    def notes(self) -> tuple[str, ...]:
        notes = []
        if self.timed_out:
            share = round(100 * sum(self.sizes[i] for i, _ in self.parts) / max(1, sum(self.sizes)))
            notes.append(f"Covered {len(self.parts)} of {self.total} parts (about {share}%) before my time limit.")
        if self.failed:
            notes.append(f"{self.failed} of {self.total} parts couldn't be summarized (the model call failed).")
        return tuple(notes)


async def map_chunks(deps: llm.TurnDeps, chunks: Sequence[str], instruction: str,
                     context: Sequence[str] = ()) -> Mapped:
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def one(index: int, chunk: str) -> tuple[int, str, Any]:
        async with gate:
            try:
                parsed = await llm.complete_json(
                    deps, prompts.summarize_messages(chunk, instruction, context, part=(index + 1, len(chunks))),
                    prompts.SUMMARY_CONTRACT, checks.parse_summary)
            except llm.LlmError:
                log.warning("map call for part %d failed", index + 1)
                return index, "failed", None
            if parsed["status"] != "ok":
                return index, parsed["status"], None
            return index, "ok", checks.check_summary(parsed, chunk, prefix=f"c{index + 1}.")

    tasks = [asyncio.create_task(one(i, chunk)) for i, chunk in enumerate(chunks)]
    done, pending = await asyncio.wait(tasks, timeout=max(0.0, deps.remaining() - REDUCE_RESERVE_SECS))
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    results = sorted(task.result() for task in done)
    parts = tuple((i, sections) for i, status, sections in results if status == "ok")
    statuses = [status for _, status, _ in results]
    return Mapped(total=len(chunks), parts=parts, sizes=tuple(len(c) for c in chunks), timed_out=bool(pending),
                  failed=statuses.count("failed"),
                  off_topic=bool(statuses) and all(s == "off_topic" for s in statuses))


async def reduce(deps: llm.TurnDeps, mapped: Mapped, source: str, instruction: str,
                 context: Sequence[str] = ()) -> Summary:
    messages = prompts.reduce_messages(items_block(mapped.pool), instruction, mapped.total, context)
    try:
        parsed = await llm.complete_json(deps, messages, prompts.REDUCE_CONTRACT, parse_reduce, llm.REDUCE_MAX_TOKENS)
    except llm.LlmError:
        log.exception("reduce failed; showing the union of the checked map items")
        return union(mapped)
    return apply_reduce(parsed, mapped, source)


def items_block(pool: Sequence[Item]) -> str:
    lines = []
    for item in pool:
        extra = f" (owner: {item.owner}; due: {item.due or 'not stated'})" if item.kind == "action" else ""
        lines.append(f"{item.item_id} [{item.kind}] {item.text}{extra}")
    return "\n".join(lines)


def parse_reduce(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("tldr"), list):
        return None
    tldr, decisions, actions = data.get("tldr") or [], data.get("decisions") or [], data.get("actions") or []
    if not all(isinstance(x, list) for x in (tldr, decisions, actions)):
        return None
    if not all(isinstance(x, Mapping) for x in (*tldr, *actions)):
        return None
    return {"tldr": [dict(x) for x in tldr], "decisions": [str(x) for x in decisions],
            "actions": [dict(x) for x in actions]}


def apply_reduce(parsed: Mapping[str, Any], mapped: Mapped, source: str) -> Summary:
    by_id = {item.item_id: item for item in mapped.pool}
    norm = g.normalise(source)
    dropped = mapped.dropped
    tldr = []
    for bullet in parsed["tldr"]:
        item = _bullet(bullet, by_id, source, norm)
        dropped += item is None
        tldr += [item] if item else []
    if not tldr:  # nothing the reduce wrote could be backed: fall back to the parts' own TL;DRs
        tldr = [i for i in mapped.pool if i.kind == "tldr"]
    decisions = [by_id[i] for i in parsed["decisions"] if by_id.get(i) and by_id[i].kind == "decision"]
    actions = [a for a in (_action(entry, by_id, source, norm) for entry in parsed["actions"]) if a]
    return checks.finalize(tldr, decisions, actions, dropped, mapped.notes())


def union(mapped: Mapped) -> Summary:
    pool = mapped.pool
    by_kind = {kind: [i for i in pool if i.kind == kind] for kind in ("tldr", "decision", "action")}
    return checks.finalize(by_kind["tldr"], by_kind["decision"], by_kind["action"], mapped.dropped,
                           (*mapped.notes(), FALLBACK_NOTE))


def _known(ids: Any, by_id: Mapping[str, Item], kind: str | None = None) -> list[Item]:
    found = [by_id.get(str(i)) for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
    return [i for i in found if i is not None and (kind is None or i.kind == kind)]


def _bullet(bullet: Mapping[str, Any], by_id: Mapping[str, Item], source: str, norm: str) -> Item | None:
    cited = _known(bullet.get("item_ids"), by_id)
    text = checks.clean_text(bullet.get("text"), source)
    if not cited or not text or g.ungrounded_tokens(text, norm):
        return None
    return Item(kind="tldr", text=checks.cut_words(text, checks.WORD_CAPS["tldr"]), snippet=cited[0].snippet,
                item_id=",".join(i.item_id for i in cited))


def _action(entry: Mapping[str, Any], by_id: Mapping[str, Item], source: str, norm: str) -> Item | None:
    cited = _known(entry.get("ids"), by_id, "action")
    if not cited:
        return None
    base = cited[0]
    text = checks.clean_text(entry.get("text"), source)
    if not text or g.ungrounded_tokens(text, norm):
        text = base.text  # the merged wording added something the source doesn't say: keep the checked one
    span = g.locate(base.snippet, norm)
    owner, due = base.owner, base.due
    if span is not None and owner == NOT_STATED:
        owner = checks.grounded_owner(checks.clean_text(entry.get("owner"), source), norm, span)
    if span is not None and not due:
        due = checks.grounded_due(checks.clean_text(entry.get("due"), source), norm, span)
    return Item(kind="action", text=checks.cut_words(text, checks.WORD_CAPS["action"]), snippet=base.snippet,
                owner=owner, due=due, item_id=",".join(i.item_id for i in cited))
