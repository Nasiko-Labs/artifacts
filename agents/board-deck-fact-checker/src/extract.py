"""Claim extraction: one tool-less model call per chunk of at most 8 slides; every claim is checked in code
(verbatim quote, value inside the quote, not a date or list number) before it is kept."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import checks
import prompts
import slides
from deps import TurnDeps
from records import Claim
from steps import ask_once

log = logging.getLogger("board_deck.extract")

MAX_CHUNKS = 20


@dataclass(frozen=True)
class Extraction:
    claims: tuple[Claim, ...] = ()
    off_topic: bool = False
    dropped: int = 0
    capped: int = 0
    not_checked: tuple[int, ...] = ()  # slide numbers skipped for time or the chunk cap


async def run(deps: TurnDeps, deck: slides.Deck) -> Extraction:
    claims: tuple[Claim, ...] = ()
    dropped, skipped, answered = 0, (), 0
    for index, chunk in enumerate(slides.chunks(deck)):
        if index >= MAX_CHUNKS or deps.past_deadline():
            skipped = (*skipped, *(s.n for s in chunk))
            continue
        payload = json.dumps([{"slide": s.n, "text": s.text} for s in chunk], ensure_ascii=False)
        messages = ({"role": "system", "content": prompts.extract_system()},
                    {"role": "user", "content": prompts.extract_user(payload)})
        answer = checks.parse_answer(await ask_once(deps, messages))
        if answer is None:
            log.info("chunk %d: unreadable extraction answer", index)
            continue
        if answer.get("status") == "off_topic" and not claims:
            return Extraction(off_topic=True)
        answered += 1
        found = checks.check_claims(checks.items(answer, "claims"), chunk, len(claims))
        claims, dropped = (*claims, *found.claims), dropped + found.dropped
    capped = checks.cap(claims)
    log.info("extracted %d claims (%d dropped by checks)", len(capped.claims), dropped)
    return Extraction(capped.claims, False, dropped, capped.capped, tuple(skipped))
