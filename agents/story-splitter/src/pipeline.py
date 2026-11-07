"""A new epic, end to end:

    outline (1 call per chunk) -> [Q3 when no actor] -> slice (1) -> check + size (code)
      -> resplit every L story (1, batched) -> order + cap (code) -> acceptance (<= 2) -> repair (<= 1)

Budget: 5 + chunks calls, the one JSON repair included. Sizes, order, caps and counts are code.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import acceptance
import outline as outline_mod
import repair
import slice as slice_mod
from checks import Ground, Report, clean, parse_all, remap
from envelope import Checks
from inputs import Request, out_of_scope_items
from llm import LlmError, TurnDeps
from order import arrange
from stories import Story

log = logging.getLogger("story-splitter.pipeline")

STILL_LARGE = "still large after one automatic split: split it by hand or add detail"
RESPLIT_FAILED = "large, and the automatic split failed this time: ask me to split this story"


@dataclass(frozen=True)
class Outcome:
    status: str                                   # ok | off_topic | failed | nothing | no_stories | ask_who
    stories: tuple[Story, ...] = ()
    skeleton: str = ""
    outline: outline_mod.Outline = field(default_factory=outline_mod.Outline)
    checks: Checks = field(default_factory=Checks)


async def start(deps: TurnDeps, request: Request, policy: Sequence[str] = ()) -> Outcome:
    try:
        found = await outline_mod.build(deps, request.epic, request.constraints, policy)
    except LlmError as e:
        log.warning("outline failed: %s", e)
        return Outcome("failed")
    if found.status == outline_mod.OFF_TOPIC:
        return Outcome("off_topic")
    if not found.capabilities:
        return Outcome("nothing", outline=found)
    found = replace(found, extra_actors=request.personas)
    if not found.actors and not request.personas:
        return Outcome("ask_who", outline=found)
    return await from_outline(deps, request, found, policy)


async def from_outline(deps: TurnDeps, request: Request, found: outline_mod.Outline,
                       policy: Sequence[str] = ()) -> Outcome:
    ground = Ground(request.epic, request.constraints, found)
    report = Report()
    try:
        sliced = await slice_mod.slice_epic(deps, ground, request.max_stories, request.sprint_sized, policy)
    except LlmError as e:
        log.warning("slice failed: %s", e)
        return Outcome("failed", outline=found)
    if sliced["status"] == slice_mod.OFF_TOPIC:
        return Outcome("off_topic", outline=found)
    out_of_scope = out_of_scope_items(request.constraints)
    stories = clean(parse_all(sliced["stories"], "s", ground, report), out_of_scope, report)
    stories = clean(await _resplit_large(deps, ground, stories, report, policy), out_of_scope, report)
    ordered = arrange(stories, request.max_stories)
    written = await acceptance.write(deps, list(ordered.stories), ground, policy)
    fixed = await repair.fix(deps, written.stories, ground)
    final = arrange(remap(fixed.stories, {}), request.max_stories)
    if not final.stories:
        return Outcome("no_stories", outline=found)
    stories = [s.with_note(STILL_LARGE) if s.size == "L" and not any("large" in n for n in s.notes) else s
               for s in final.stories]
    checks = Checks(dropped=(*report.dropped, *fixed.dropped), merged=tuple(report.merged), capped=ordered.capped,
                    actors_replaced=report.actors_replaced, dropped_criteria=fixed.dropped_criteria,
                    broken_cycles=ordered.broken_cycles)
    return Outcome("ok", tuple(stories), final.skeleton, found, checks)


async def _resplit_large(deps: TurnDeps, ground: Ground, stories: list[Story], report: Report,
                         policy: Sequence[str]) -> list[Story]:
    """Every L story is split once, in one batched call; a failed call leaves them with a note."""
    targets = [s for s in stories if s.size == "L" and not s.is_spike]
    if not targets:
        return stories
    try:
        by_key = await slice_mod.resplit(deps, ground, targets, policy)
    except LlmError as e:
        log.warning("resplit failed: %s", e)
        by_key = {}
    result = stories
    for target in targets:
        children = slice_mod.children_of(target, by_key.get(target.key) or [], ground, report)
        if children:
            result = slice_mod.swap_in(result, target.key, children)
        else:
            result = [s.with_note(RESPLIT_FAILED) if s.key == target.key else s for s in result]
    return result
