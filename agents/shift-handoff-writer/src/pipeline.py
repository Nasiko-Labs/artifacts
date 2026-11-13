"""One handoff run: extract (map) -> reconcile (code) -> check (one repair) -> classify (code) -> compose (one
call). Also `add a note`: one extract call over the new entry only, then the same code steps."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

import checks
import duetime
import grounding
import handoff as hf
from compose import compose
from entries import Entry, parse
from extract import extract, extract_note
from llm import TurnDeps
from reconcile import reconcile

CLOSED_EARLY = ("Covered note entries {first} to {last} of {total}: the rest didn't fit in this turn's time or "
                "call budget.")
HEADLINE_STALE = "The headline is from the earlier notes."


@dataclass(frozen=True)
class Outcome:
    status: str                          # ok | off_topic | not_notes | failed | nothing
    handoff: hf.Handoff | None = None


def _notes_norm(entries: Sequence[Entry]) -> str:
    return grounding.normalise("\n".join(e.text for e in entries))


async def build(deps: TurnDeps, entries: list[Entry], instruction: str, policy: Sequence[str],
                now: datetime) -> Outcome:
    extraction = await extract(deps, entries, instruction)
    if extraction.status != "ok":
        return Outcome(extraction.status)
    rec = reconcile(list(extraction.items), list(extraction.instructions), entries)
    if not rec.items and not rec.instructions:
        return Outcome("nothing")
    at = duetime.handoff_time(instruction, now)
    by_id = {e.id: e for e in entries}
    norm = _notes_norm(entries)
    items = await checks.ground_items(deps, rec.items, by_id)
    items = hf.classify(items, by_id, at)
    instructions = checks.ground_instructions(rec.instructions, norm)
    composed = await compose(deps, items, instructions, norm, policy)
    notes = []
    if extraction.closed_early and extraction.covered:
        notes.append(CLOSED_EARLY.format(first=extraction.covered[0], last=extraction.covered[-1],
                                         total=len(entries)))
    if composed.note:
        notes.append(composed.note)
    return Outcome("ok", hf.Handoff(
        entries=tuple(entries), items=items, instructions=instructions, handoff_at=at.isoformat(timespec="minutes"),
        instruction=instruction, next_shift=duetime.next_shift(instruction), headline=composed.headline,
        know_order=composed.know_order, dropped=rec.dropped, windows=extraction.windows, done=extraction.done,
        notes=tuple(notes)))


async def add_note(deps: TurnDeps, stored: hf.Handoff, text: str) -> Outcome:
    new = parse(text, start=len(stored.entries) + 1)
    if not new:
        return Outcome("nothing")
    extraction = await extract_note(deps, new)
    if extraction.status != "ok":
        return Outcome(extraction.status)
    entries = [*stored.entries, *new]
    rec = reconcile(list(extraction.items), list(extraction.instructions), entries, stored.items,
                    stored.instructions)
    by_id = {e.id: e for e in entries}
    norm = _notes_norm(entries)
    items = tuple(checks.fallback(i, checks.own_norm(i, by_id)) for i in rec.items)
    notes = tuple(dict.fromkeys((*stored.notes, HEADLINE_STALE))) if stored.headline else stored.notes
    return Outcome("ok", replace(stored, entries=tuple(entries), items=hf.classify(items, by_id, stored.at),
                                 instructions=checks.ground_instructions(rec.instructions, norm),
                                 dropped=stored.dropped + rec.dropped, notes=notes))
