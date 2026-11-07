"""The grounding check: numbers, refs, times and names in an item's `what` and `next_step` (and in an
instruction's text) must appear in the notes. Failing items get one repair call together; whatever still
fails is shown as its quoted snippet instead of the paraphrase."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import grounding
import prompts
from entries import Entry
from items import Instruction, Item
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("shift_handoff.checks")

MAX_REPAIR_ITEMS = 20


def _fails(item: Item, notes_norm: str) -> bool:
    return bool(grounding.ungrounded(item.what, notes_norm) or grounding.ungrounded(item.next_step, notes_norm))


def fallback(item: Item, notes_norm: str) -> Item:
    """The snippet stands in for an ungrounded paraphrase; an ungrounded next step is cleared."""
    what_ok = not grounding.ungrounded(item.what, notes_norm)
    step_ok = not grounding.ungrounded(item.next_step, notes_norm)
    return replace(item, what=item.what if what_ok else f'"{item.snippet}"',
                   next_step=item.next_step if step_ok else None, fallback=item.fallback or not what_ok)


def _validate(ids: set[str]):
    def check(data: Any) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return None
        out = {}
        for raw in data["items"]:
            if isinstance(raw, dict) and raw.get("id") in ids and isinstance(raw.get("what"), str):
                step = raw.get("next_step")
                out[raw["id"]] = (raw["what"].strip()[:300], step.strip()[:200] if isinstance(step, str) else None)
        return {"items": out}
    return check


async def ground_items(deps: TurnDeps, items: tuple[Item, ...], by_id: dict[str, Entry],
                       notes_norm: str) -> tuple[Item, ...]:
    failing = [i for i in items if _fails(i, notes_norm)][:MAX_REPAIR_ITEMS]
    fixes: dict[str, tuple[str, str | None]] = {}
    if failing and deps.traceparent and deps.can_call():
        payload = [{"id": i.id, "what": i.what, "next_step": i.next_step,
                    "entries": [by_id[e].text for e in i.entries if e in by_id][-3:]} for i in failing]
        try:
            data = await complete_json(deps, prompts.repair_items_messages(payload), prompts.REPAIR_ITEMS_CONTRACT,
                                       _validate({i.id for i in failing}))
            fixes = data["items"]
        except LlmError as e:
            log.warning("grounding repair failed: %s", e)
    out = []
    for item in items:
        if item.id in fixes:
            what, step = fixes[item.id]
            item = replace(item, what=what, next_step=step)
        out.append(fallback(item, notes_norm) if _fails(item, notes_norm) else item)
    return tuple(out)


def ground_instructions(instructions: tuple[Instruction, ...], notes_norm: str) -> tuple[Instruction, ...]:
    return tuple(i if not grounding.ungrounded(i.text, notes_norm) else replace(i, text=f'"{i.snippet}"', fallback=True)
                 for i in instructions)
