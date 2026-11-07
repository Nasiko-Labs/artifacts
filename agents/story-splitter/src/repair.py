"""Invented facts: a number, month or system name in a story or criterion that the epic never mentions. All
such texts are repaired in one call (the turn's one repair); what is still invented after it is dropped: a
criterion on its own, a story when its title, want or benefit is affected."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace

import prompts
from acceptance import valid_shape
from checks import Ground
from grounding import invented
from llm import LlmError, TurnDeps, call, load
from stories import Story

log = logging.getLogger("story-splitter.repair")

STORY_FIELDS = ("title", "i_want", "so_that")


@dataclass(frozen=True)
class Fixed:
    stories: list[Story]
    dropped: list[str] = field(default_factory=list)   # story titles
    dropped_criteria: int = 0
    repaired: int = 0


def flagged(stories: list[Story], ground: Ground) -> dict[str, tuple[str, list[str]]]:
    """item id -> (text, invented tokens) for every text that needs a repair."""
    found: dict[str, tuple[str, list[str]]] = {}
    for story in stories:
        texts = [(f"{story.key}.{name}", getattr(story, name)) for name in STORY_FIELDS]
        texts += [(f"{story.key}.a{i}", c) for i, c in enumerate(story.acceptance)]
        for item_id, text in texts:
            bad = invented(text, ground.full)
            if bad:
                found[item_id] = (text, bad)
    return found


async def _ask(deps: TurnDeps, items: dict[str, tuple[str, list[str]]], ground: Ground) -> dict[str, str]:
    if not deps.traceparent or not deps.has_time() or not deps.budget.take_repair():
        return {}
    block = "\n".join(f"{i}: {text} (invented: {', '.join(bad)})" for i, (text, bad) in items.items())
    try:
        raw = await call(deps, prompts.repair_messages(ground.epic, ground.constraints, block))
    except LlmError as e:
        log.warning("repair call failed: %s", e)
        return {}
    data = load(raw)
    fixes = data.get("fixes") if isinstance(data, dict) else None
    if not isinstance(fixes, list):
        log.info("repair answer was not usable: %s", json.dumps(raw)[:200])
        return {}
    return {f["id"]: " ".join(f["text"].split()) for f in fixes
            if isinstance(f, dict) and f.get("id") in items and isinstance(f.get("text"), str) and f["text"].strip()}


async def fix(deps: TurnDeps, stories: list[Story], ground: Ground) -> Fixed:
    items = flagged(stories, ground)
    if not items:
        return Fixed(stories)
    answers = await _ask(deps, items, ground)
    good = {i: t for i, t in answers.items() if not invented(t, ground.full)}
    kept: list[Story] = []
    dropped: list[str] = []
    dropped_criteria = 0
    for story in stories:
        updated = story
        for name in STORY_FIELDS:
            item_id = f"{story.key}.{name}"
            if item_id in items:
                updated = replace(updated, **{name: good[item_id]}) if item_id in good else updated
        if any(f"{story.key}.{name}" in items and f"{story.key}.{name}" not in good for name in STORY_FIELDS):
            dropped.append(f"\"{story.title}\" (used a number or name the epic doesn't give)")
            continue
        criteria = []
        for i, text in enumerate(story.acceptance):
            item_id = f"{story.key}.a{i}"
            if item_id not in items:
                criteria.append(text)
            elif item_id in good and valid_shape(good[item_id]):
                criteria.append(good[item_id].rstrip("."))
            else:
                dropped_criteria += 1
        kept.append(replace(updated, acceptance=tuple(criteria)))
    return Fixed(kept, dropped, dropped_criteria, len(good))
