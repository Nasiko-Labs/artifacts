"""The slice and resplit calls, and how a split story's children take its place in the list."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import prompts
from checks import Ground, Report, parse_all, remap
from llm import TurnDeps, complete_json
from stories import Story

log = logging.getLogger("story-splitter.slice")

OFF_TOPIC = "off_topic"
MAX_CHILDREN = 3


def _validate_slice(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return {"status": OFF_TOPIC, "stories": []}
    stories = data.get("stories")
    return {"status": "ok", "stories": stories} if isinstance(stories, list) else None


def options_text(max_stories: int, sprint_sized: bool) -> str:
    text = f"Write at most {max_stories} stories, in the order they should be built."
    if sprint_sized:
        text += " Keep every story small enough for one sprint: few behaviours, one screen, one integration at most."
    return text


async def slice_epic(deps: TurnDeps, ground: Ground, max_stories: int, sprint_sized: bool,
                     policy: Sequence[str] = ()) -> dict:
    """{"status", "stories": raw dicts}. Raises LlmError when the call failed."""
    messages = prompts.slice_messages(ground.epic, ground.constraints, ground.outline.block(),
                                      options_text(max_stories, sprint_sized), policy)
    return await complete_json(deps, messages, prompts.SLICE_CONTRACT, _validate_slice)


def resplit_line(story: Story) -> str:
    return (f"{story.key}: {story.title} | As a {story.as_a}, I want to {story.i_want}, so that {story.so_that} | "
            f"slice_pattern: {story.slice_pattern} | basis: {', '.join(story.basis) or 'none'}")


async def resplit(deps: TurnDeps, ground: Ground, targets: list[Story], policy: Sequence[str] = ()) -> dict[str, list]:
    """key -> raw child stories (2-3 each). Raises LlmError when the call failed."""
    keys = {s.key for s in targets}

    def validate(data: Any) -> dict | None:
        if not isinstance(data, dict) or not isinstance(data.get("splits"), list):
            return None
        found = {e["key"]: e["stories"] for e in data["splits"]
                 if isinstance(e, dict) and e.get("key") in keys and isinstance(e.get("stories"), list)}
        return {"by_key": found} if found else None

    messages = prompts.resplit_messages(ground.epic, ground.constraints, ground.outline.block(),
                                        "\n".join(resplit_line(s) for s in targets), policy)
    answer = await complete_json(deps, messages, prompts.RESPLIT_CONTRACT, validate)
    return answer["by_key"]


def children_of(parent: Story, raws: list, ground: Ground, report: Report) -> list[Story]:
    """Checked children of `parent` (at most three); they keep its actor when they name none."""
    kids = parse_all(raws[:MAX_CHILDREN], f"{parent.key}.", ground, report)
    result = []
    for i, kid in enumerate(kids):
        deps = kid.depends_on if i else tuple(dict.fromkeys((*parent.depends_on, *kid.depends_on)))
        if not deps:
            deps = parent.depends_on
        actor = parent.as_a if kid.as_a.startswith("[") else kid.as_a
        result.append(replace(kid, depends_on=deps, as_a=actor, basis=kid.basis or parent.basis))
    return result if len(result) >= 2 else []


def swap_in(stories: list[Story], parent_key: str, children: list[Story]) -> list[Story]:
    """`children` replace the parent at its place; whatever depended on the parent depends on all of them."""
    if not children:
        return stories
    result: list[Story] = []
    for story in stories:
        result += children if story.key == parent_key else [story]
    return remap(result, {parent_key: tuple(c.key for c in children)})
