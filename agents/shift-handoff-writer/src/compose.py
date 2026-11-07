"""The compose step (one call): the headline sentence and the model's preferred order of the instructions.
Everything else in the handoff is code. A headline naming a number, ref, time or name the notes don't
contain is dropped, not shown."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import grounding
import prompts
from items import RESOLVED, Instruction, Item, label, owner_text
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("shift_handoff.compose")

HEADLINE_MAX_WORDS = 30
MAX_FACT_ITEMS = 25


@dataclass(frozen=True)
class Composed:
    headline: str | None
    know_order: tuple[str, ...] = ()
    note: str | None = None


def facts(items: tuple[Item, ...], instructions: tuple[Instruction, ...]) -> dict:
    live = [i for i in items if i.status != RESOLVED]
    ranked = sorted(live, key=lambda i: not i.hot)[:MAX_FACT_ITEMS]
    return {
        "items": [{"item": label(i), "status": i.status, "hot": i.hot, "owner": owner_text(i), "due": i.due,
                   "why_hot": [r.cue for r in i.hot_reasons], "waiting_on": i.waiting_on} for i in ranked],
        "instructions": [{"id": k.id, "kind": k.kind, "text": k.text} for k in instructions],
        "resolved_count": sum(1 for i in items if i.status == RESOLVED),
    }


def _validate(known_ids: set[str]):
    def check(data: Any) -> dict | None:
        if not isinstance(data, dict):
            return None
        headline = data.get("headline")
        headline = " ".join(headline.split()) if isinstance(headline, str) else ""
        order = [k for k in data.get("know") or [] if isinstance(k, str) and k in known_ids]
        return {"headline": headline, "know": order}
    return check


def grounded_headline(headline: str, notes_norm: str, allowed: frozenset[str]) -> str | None:
    if not headline or len(headline.split()) > HEADLINE_MAX_WORDS:
        return None
    return None if grounding.ungrounded(headline, notes_norm, allowed) else headline


async def compose(deps: TurnDeps, items: tuple[Item, ...], instructions: tuple[Instruction, ...],
                  notes_norm: str, policy: Sequence[str]) -> Composed:
    if not deps.can_call():
        return Composed(None, note="The headline was skipped: the turn ran out of time.")
    counts = frozenset(str(n) for n in range(len(items) + 1))
    try:
        data = await complete_json(deps, prompts.compose_messages(facts(items, instructions), policy),
                                   prompts.COMPOSE_CONTRACT, _validate({k.id for k in instructions}))
    except LlmError as e:
        log.warning("compose failed: %s", e)
        return Composed(None, note="The headline is missing: the model call for it failed.")
    headline = grounded_headline(data["headline"], notes_norm, counts)
    if data["headline"] and headline is None:
        log.info("headline dropped; not in the notes: %s",
                 grounding.ungrounded(data["headline"], notes_norm, counts) or "too long")
    note = None if headline or not data["headline"] else "The headline was left out: it named something not in your notes."
    return Composed(headline, tuple(data["know"]), note)
