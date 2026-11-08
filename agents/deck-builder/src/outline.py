"""The model calls that write words: a new deck (outline), an edit, and the one repair. Each returns a
validated Deck, the off-topic sentinel, or None. The deck JSON is fenced as data on edit and repair."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import prompts
from deps import TurnDeps
from fence import fence
from llm import Budget, ask_json
from models import Deck, parse_deck

OFF_TOPIC = "off_topic"
log = logging.getLogger("deck_builder.outline")


@dataclass(frozen=True)
class Brief:
    request: str
    notes: str                 # the notes, or the merged POINTS from the map step
    notes_kind: str            # "notes" | "points"
    tables: list[dict]
    given: list[str]
    count: int


def _answer(data: dict | None) -> Deck | str | None:
    if not data:
        log.warning("model answer was not a JSON object")
        return None
    if data.get("status") == OFF_TOPIC:
        return OFF_TOPIC
    deck = parse_deck(data.get("deck") if isinstance(data.get("deck"), dict) else data)
    if deck is None:
        log.warning("model answer is not a usable deck (keys: %s)", sorted(data)[:8])
    return deck


def _data_block(tables: list[dict]) -> str:
    if not tables:
        return ""
    rows = [f"{t['name']}: " + json.dumps([t["columns"], *t["rows"][:20]]) for t in tables]
    return "\n" + fence("data", "\n".join(rows))


async def new_deck(deps: TurnDeps, budget: Budget, brief: Brief) -> Deck | str | None:
    user = (f"Request: {brief.request}\nSlide count: {brief.count}\n{prompts.tables_block(brief.tables)}\n"
            f"{prompts.given_block(brief.given)}\n{fence(brief.notes_kind, brief.notes or '(none)')}"
            f"{_data_block(brief.tables)}")
    messages = ({"role": "system", "content": prompts.OUTLINE_SYSTEM}, {"role": "user", "content": user})
    return _answer(await ask_json(deps, budget, messages))


async def edit_deck(deps: TurnDeps, budget: Budget, deck: Deck, instruction: str, tables: list[dict],
                    given: list[str], notes: str) -> Deck | str | None:
    user = (f"Edit instruction: {instruction}\n{prompts.tables_block(tables)}\n{prompts.given_block(given)}\n"
            f"{fence('deck', json.dumps(deck.dump()))}\n{fence('notes', notes or '(none)')}{_data_block(tables)}")
    messages = ({"role": "system", "content": prompts.EDIT_SYSTEM}, {"role": "user", "content": user})
    return _answer(await ask_json(deps, budget, messages))


async def repair_deck(deps: TurnDeps, budget: Budget, deck: Deck, problems: list[str], tables: list[dict],
                      given: list[str]) -> Deck | None:
    listed = "\n".join(f"- {p}" for p in problems[:30])
    user = (f"Problems to fix:\n{listed}\n{prompts.tables_block(tables)}\n{prompts.given_block(given)}\n"
            f"{fence('deck', json.dumps(deck.dump()))}")
    messages = ({"role": "system", "content": prompts.REPAIR_SYSTEM}, {"role": "user", "content": user})
    fixed = _answer(await ask_json(deps, budget, messages))
    return fixed if isinstance(fixed, Deck) else None
