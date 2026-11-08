"""The deck: read through the file tools (one bounded tool loop), or pasted. The model only finds and reads
the file; the deck text is taken in code from the read tool's own output (the longest text it returned), so
every claim quote is checked against what the tool really returned."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import checks
import prompts
import slides
from deps import TurnDeps
from discovery import Catalog
from steps import StepResult, run_step

log = logging.getLogger("board_deck.deck")

DECK_LOOP_CALLS = 4  # plus one repair call
MIN_DECK_CHARS = 40


@dataclass(frozen=True)
class DeckRead:
    status: str  # ok | off_topic | not_found | no_tools | unreadable | paused | skipped
    deck: slides.Deck | None = None
    step: StepResult | None = None


async def read(deps: TurnDeps, catalog: Catalog, query: str, links: Sequence[str]) -> DeckRead:
    tools = catalog.tools_for("deck")
    step = await run_step(deps, tools, catalog.writes, prompts.deck_system(links), query, DECK_LOOP_CALLS)
    if step.status in ("off_topic", "no_tools", "paused", "skipped"):
        return DeckRead(step.status, step=step)
    text = deck_text(step.results)
    if not text:
        return DeckRead("not_found" if step.status in ("ok", "not_found") else step.status, step=step)
    title = checks.text_of(step.answer.get("title"))
    if not title or title not in step.seen_text:
        title = "the deck"
    return DeckRead("ok", slides.split(text, title), step)


def deck_text(results: Sequence[tuple[str, str]]) -> str:
    """The longest text any read returned: a JSON result's longest string value, or the raw text."""
    best = ""
    for _, body in results:
        for candidate in _texts(body):
            if len(candidate) > len(best):
                best = candidate
    return best if len(best) >= MIN_DECK_CHARS else ""


def _texts(body: str) -> Iterator[str]:
    try:
        data = json.loads(body)
    except ValueError:
        yield body
        return
    yield from _strings(data)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        nested = _maybe_json(value)
        if nested is not None:
            yield from _strings(nested)
        else:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _maybe_json(text: str) -> Any:
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None
