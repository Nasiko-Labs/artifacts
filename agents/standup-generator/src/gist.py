"""The optional one-clause gist per line, from its title, checked in code to add nothing.

At most MAX_CALLS batched LLM calls of BATCH lines each. A gist is kept only when it is one clause of
at most MAX_WORDS words with no digit, id, link or handle, and every word that is capitalised (after
the first) appears in the title. A line whose gist fails the check shows its raw title.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping

import prompts
from checks import parse_answer
from deps import HARD_DEADLINE_SECS, TurnDeps
from llm import ask_once

log = logging.getLogger("standup.gist")

MAX_WORDS = 12
BATCH = 40
MAX_CALLS = 2
_FORBIDDEN = re.compile(r"\d|#|@|/|https?:|\[|\]|`|\*|_{2}|\n")
_WORD = re.compile(r"[A-Za-z][\w'-]*")


def check(gist: object, title: str) -> str | None:
    """The gist when it passes every check, else None."""
    if not isinstance(gist, str):
        return None
    text = gist.strip().rstrip(".")
    words = text.split()
    if not words or len(words) > MAX_WORDS or _FORBIDDEN.search(text) or ";" in text:
        return None
    title_words = {w.casefold() for w in _WORD.findall(title)}
    for word in _WORD.findall(text)[1:]:
        if word[0].isupper() and word.casefold() not in title_words:
            return None
    return text


async def gists(deps: TurnDeps, titles: Mapping[str, str]) -> dict[str, str]:
    """Line id → checked gist, for the ids whose gist passed."""
    ids = [k for k, v in titles.items() if v.strip()]
    kept: dict[str, str] = {}
    for start in range(0, min(len(ids), BATCH * MAX_CALLS), BATCH):
        if deps.past(HARD_DEADLINE_SECS):
            log.info("gist skipped: past the hard deadline")
            break
        batch = {k: titles[k] for k in ids[start:start + BATCH]}
        kept |= await _one_call(deps, batch)
    return kept


async def _one_call(deps: TurnDeps, batch: Mapping[str, str]) -> dict[str, str]:
    messages = ({"role": "system", "content": prompts.GIST_SYSTEM},
                {"role": "user", "content": prompts.fence(json.dumps(batch, ensure_ascii=False))})
    answer = parse_answer(await ask_once(deps, messages)) or {}
    kept = {}
    for line_id, title in batch.items():
        gist = check(answer.get(line_id), title)
        if gist is not None:
            kept[line_id] = gist
    log.info("gist: %d of %d lines kept", len(kept), len(batch))
    return kept

