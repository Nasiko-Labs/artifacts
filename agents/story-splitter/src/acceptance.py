"""Acceptance criteria: batched calls (at most 6 stories each), validated in code. A criterion must read
Given ... when ... then ..., in that order, in at most 40 words. Spikes keep their fixed criterion."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import prompts
from checks import Ground
from llm import LlmError, TurnDeps, complete_json
from stories import Story

log = logging.getLogger("story-splitter.acceptance")

BATCH_SIZE = 6
MIN_CRITERIA, MAX_CRITERIA = 2, 5
MAX_CRITERION_WORDS = 40
_SHAPE = re.compile(r"\bgiven\b.+\bwhen\b.+\bthen\b", re.IGNORECASE | re.DOTALL)
FEW_NOTE = "only {n} acceptance criterion passed the checks; add more by hand"
FAILED_NOTE = "acceptance criteria could not be written this time; ask \"add acceptance to story {n}\""


@dataclass(frozen=True)
class Written:
    stories: list[Story]
    rejected: int = 0      # criteria dropped for shape
    failed: bool = False   # a call failed: some stories have no criteria


def valid_shape(text: str) -> bool:
    return bool(_SHAPE.search(text)) and len(text.split()) <= MAX_CRITERION_WORDS


def story_line(story: Story, ground: Ground) -> str:
    texts = {i["id"]: i.get("text") or i.get("name") for group in (
        ground.outline.capabilities, ground.outline.rules, ground.outline.unknowns) for i in group}
    basis = "; ".join(f"{b}: {texts[b]}" for b in story.basis if b in texts) or "none"
    flags = [name for name, on in (("new_data", story.facts.new_data), ("permissions", story.facts.permissions),
                                   ("touches_external", story.facts.touches_external)) if on]
    return (f"{story.key}: {story.title} | As a {story.as_a}, I want to {story.i_want}, so that {story.so_that} | "
            f"basis: {basis} | facts: {', '.join(flags) or 'none'}")


def _validate(keys: set[str]):
    def check(data: Any) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("acceptance"), list):
            return None
        found = {}
        for entry in data["acceptance"]:
            if isinstance(entry, dict) and entry.get("key") in keys and isinstance(entry.get("criteria"), list):
                found[entry["key"]] = [c for c in entry["criteria"] if isinstance(c, str)]
        return {"by_key": found} if found else None
    return check


def _keep(raw: list[str]) -> tuple[tuple[str, ...], int]:
    kept, rejected = [], 0
    for text in raw:
        line = " ".join(text.split()).rstrip(".")
        if not valid_shape(line) or line in kept:
            rejected += 1
            continue
        kept.append(line)
    return tuple(kept[:MAX_CRITERIA]), rejected


async def write(deps: TurnDeps, stories: list[Story], ground: Ground, policy: Sequence[str] = (),
                instruction: str = "") -> Written:
    """Criteria for every story that is not a spike; at most two calls."""
    todo = [s for s in stories if not s.is_spike]
    batches = [todo[i:i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)][:2]

    async def one(batch: list[Story]) -> dict:
        block = "\n".join(story_line(s, ground) for s in batch)
        if instruction:
            block += f"\nREQUEST: {instruction}"
        messages = prompts.acceptance_messages(ground.epic, ground.constraints, block, policy)
        return await complete_json(deps, messages, prompts.ACCEPTANCE_CONTRACT, _validate({s.key for s in batch}))

    answers = await asyncio.gather(*(one(b) for b in batches), return_exceptions=True)
    by_key: dict[str, list[str]] = {}
    failed = False
    for answer in answers:
        if isinstance(answer, LlmError):
            log.warning("acceptance call failed: %s", answer)
            failed = True
        elif isinstance(answer, BaseException):
            raise answer
        else:
            by_key.update(answer["by_key"])
    return _apply(stories, by_key, failed)


def _apply(stories: list[Story], by_key: dict[str, list[str]], failed: bool) -> Written:
    result, rejected = [], 0
    for n, story in enumerate(stories, start=1):
        if story.is_spike:
            result.append(story)
            continue
        kept, dropped = _keep(by_key.get(story.key) or [])
        rejected += dropped
        updated = replace(story, acceptance=kept)
        if not kept and story.key not in by_key:
            updated = updated.with_note(FAILED_NOTE.format(n=n)) if failed else updated
        elif len(kept) < MIN_CRITERIA:
            updated = updated.with_note(FEW_NOTE.format(n=len(kept)))
        result.append(updated)
    return Written(result, rejected, failed)


def add_to(story: Story, criteria: list[str]) -> tuple[Story, int]:
    """New criteria appended to a story (refinement), capped at five."""
    kept, rejected = _keep(criteria)
    fresh = tuple(c for c in kept if c not in story.acceptance)
    return replace(story, acceptance=(*story.acceptance, *fresh)[:MAX_CRITERIA]), rejected
