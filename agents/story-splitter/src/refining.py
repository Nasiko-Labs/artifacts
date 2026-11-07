"""Refinements on the stored stories. Zero calls: drop, rename, move, max N, which first, show all, as json.
One call (at most two per turn, no third): split story N (resplit + acceptance), merge A and B (acceptance
for the merged story; facts are the union, size recomputed), add acceptance to story N."""

from __future__ import annotations

import logging
from dataclasses import replace

import acceptance
import prompts
import refine
import render
import repair
import slice as slice_mod
from checks import Ground, Report, remap
from envelope import Checks
from inputs import HARD_MAX_STORIES
from llm import LlmError
from order import arrange, chain
from outline import Outline
from stories import Story, from_states
from turns import Scope, result

log = logging.getLogger("story-splitter.refining")

REFINE_MAX_CALLS = 2


def _ground(state: dict) -> Ground:
    return Ground(state.get("epic") or "", tuple(state.get("constraints") or ()), Outline.from_dict(state.get("outline")))


def _done(state: dict, scope: Scope, stories: list[Story], note: str, limit: int = HARD_MAX_STORIES,
          choose_skeleton: bool = False, checks: Checks | None = None) -> dict:
    ordered = arrange(stories, limit, choose_skeleton)
    capped = Checks(capped=ordered.capped) if checks is None else replace(checks, capped=ordered.capped)
    return result(list(ordered.stories), ordered.skeleton, Outline.from_dict(state.get("outline")),
                  state.get("epic") or "", state.get("epic_words") or 0, capped, scope, (note,),
                  {"max_stories": min(limit, HARD_MAX_STORIES)})


async def refine_turn(state: dict, scope: Scope) -> dict:
    command = scope.route.command
    stories = from_states(state.get("stories"))
    env = state.get("envelope") or {}
    problem = refine.missing(command, len(stories))
    if problem:
        return {"reply": problem}
    if command.kind == "json":
        return {"reply": render.render_json(env), "data": env}
    if command.kind == "all":
        return {"reply": render.render(env, state.get("epic") or ""), "data": env}
    if command.kind == "first":
        skeleton = state.get("skeleton") or (stories[0].key if stories else "")
        numbers = {s.key: n for n, s in enumerate(stories, start=1)}
        path = [numbers[s.key] for s in chain(stories, skeleton)]
        return {"reply": render.render_first(env, path), "data": env}
    if command.kind == "drop":
        kept, note = refine.drop(stories, command.a)
        return _done(state, scope, kept, note) if kept else {"reply": "That would leave no stories; nothing dropped."}
    if command.kind == "rename":
        renamed, note = refine.rename(stories, command.a, command.text)
        return _done(state, scope, renamed, note)
    if command.kind == "move":
        moved, note = refine.move(stories, command.a, command.b)
        return _done(state, scope, moved, note)
    if command.kind == "max":
        cut = max(0, len(stories) - command.a)
        note = (f"_Kept the first {command.a} stories in dependency order; cut {cut}._" if cut else
                f"_There are only {len(stories)} stories, so nothing was cut._")
        return _done(state, scope, stories, note, limit=command.a)
    if not scope.deps.traceparent:
        return {"reply": prompts.NO_TRACEPARENT}
    scope.deps.budget.max_calls = REFINE_MAX_CALLS
    try:
        return await _with_model(state, scope, command, stories)
    except LlmError as e:
        log.warning("refinement failed: %s", e)
        return {"reply": prompts.MODEL_FAILED + " Your stories are unchanged."}


async def _with_model(state: dict, scope: Scope, command: refine.Command, stories: list[Story]) -> dict:
    ground = _ground(state)
    policy = scope.incoming.policy
    if command.kind == "split":
        return await _split(state, scope, ground, stories, command.a)
    if command.kind == "merge":
        return await _merge(state, scope, ground, stories, command.a, command.b)
    story = stories[command.a - 1]
    topic = command.text or "an edge case"
    instruction = (f"Write 1 or 2 NEW criteria for story {story.key} covering: {prompts.strip_markers(topic)}. "
                   "Do not repeat: " + " | ".join(story.acceptance))
    written = await acceptance.write(scope.deps, [story], ground, policy, instruction)
    if written.failed:
        raise LlmError("acceptance call failed")
    updated, _ = acceptance.add_to(story, list(written.stories[0].acceptance))
    fixed = await repair.fix(scope.deps, [updated], ground)
    if not fixed.stories:
        return {"reply": "The new criteria used numbers or names the epic doesn't give, so I added none."}
    added = len(fixed.stories[0].acceptance) - len(story.acceptance)
    result_stories = [fixed.stories[0] if s.key == story.key else s for s in stories]
    note = (f"_Added {added} acceptance criteria to story {command.a}; size recomputed._" if added > 0 else
            f"_No new criterion for story {command.a} passed the checks._")
    return _done(state, scope, result_stories, note)


async def _split(state: dict, scope: Scope, ground: Ground, stories: list[Story], n: int) -> dict:
    target = stories[n - 1]
    if target.is_spike:
        return {"reply": f"Story {n} is a spike (a time-boxed question); it can't be split further."}
    report = Report()
    by_key = await slice_mod.resplit(scope.deps, ground, [target], scope.incoming.policy)
    children = slice_mod.children_of(target, by_key.get(target.key) or [], ground, report)
    if not children:
        return {"reply": f"I couldn't split story {n} into thinner stories that pass the checks; it's unchanged."}
    written = await acceptance.write(scope.deps, children, ground, scope.incoming.policy)
    fixed = await repair.fix(scope.deps, written.stories, ground)
    if len(fixed.stories) < 2:
        return {"reply": f"The split of story {n} didn't pass the checks; it's unchanged."}
    note = f"_Split story {n} into {len(fixed.stories)} stories; sizes recomputed._"
    checks = Checks(dropped=tuple(fixed.dropped), actors_replaced=report.actors_replaced,
                    dropped_criteria=fixed.dropped_criteria)
    return _done(state, scope, slice_mod.swap_in(stories, target.key, fixed.stories), note, checks=checks)


def merged_story(a: Story, b: Story) -> Story:
    benefit = a.so_that if not a.so_that.startswith("[") else b.so_that
    deps = tuple(d for d in dict.fromkeys((*a.depends_on, *b.depends_on)) if d not in (a.key, b.key))
    return Story(key=f"{a.key}+{b.key}", title=f"{a.title} and {b.title[:1].lower()}{b.title[1:]}", as_a=a.as_a,
                 i_want=f"{a.i_want} and {b.i_want}", so_that=benefit, slice_pattern=a.slice_pattern,
                 basis=tuple(dict.fromkeys((*a.basis, *b.basis))), depends_on=deps, facts=a.facts.union(b.facts))


async def _merge(state: dict, scope: Scope, ground: Ground, stories: list[Story], a: int, b: int) -> dict:
    first, second = sorted((a, b))
    one, two = stories[first - 1], stories[second - 1]
    merged = merged_story(one, two)
    written = await acceptance.write(scope.deps, [merged], ground, scope.incoming.policy)
    criteria = written.stories[0].acceptance or (*one.acceptance, *two.acceptance)[:acceptance.MAX_CRITERIA]
    merged = replace(merged, acceptance=tuple(criteria), notes=())
    fixed = await repair.fix(scope.deps, [merged], ground)
    if not fixed.stories:
        return {"reply": f"The merged story used facts the epic doesn't give; stories {a} and {b} are unchanged."}
    rest = [fixed.stories[0] if s.key == one.key else s for s in stories if s.key != two.key]
    rest = remap(rest, {one.key: merged.key, two.key: merged.key})
    return _done(state, scope, rest, f"_Merged stories {first} and {second}; size recomputed from both._")
