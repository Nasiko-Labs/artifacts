"""The model steps: draft a new set or revise variants (one call, at most one repair, then code fixes),
and read a voice profile from a writing sample (one call)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace

import prompts
from angles import Angle
from checks import Basis, prepare, problems
from fixes import finalize
from grounding import Ground
from inputs import GOALS
from llm import Budget, TurnDeps, ask_json
from models import Answer, Variant, parse_answer
from voice import Voice, from_sample

log = logging.getLogger("social_post_drafter_stateless.drafting")


@dataclass(frozen=True)
class Job:
    request: str                       # the user's words
    material: str                      # pasted material (data), "" when it is all in the request
    facts: tuple[str, ...]             # verbatim facts mapped from a long article
    org: tuple[str, ...]               # organization instructions: rules, never facts
    platforms: tuple[str, ...]         # the platform of each expected variant, in order
    angles: tuple[Angle, ...]          # the angle of each expected variant, in order
    goal: str | None
    voice: Voice
    ground: tuple[str, ...]            # everything that may supply a fact
    current: tuple[Variant, ...] = ()  # revision: the variants being revised (in order)


@dataclass(frozen=True)
class Composed:
    status: str                        # ok | needs_info | off_topic | private_message | publish | failed
    variants: tuple[Variant, ...] = ()
    notes: tuple[str, ...] = ()
    repaired: bool = False
    goal: str | None = None


async def compose(deps: TurnDeps, budget: Budget, job: Job) -> Composed:
    messages = ({"role": "system", "content": prompts.system(job.org)},
                {"role": "user", "content": _user(job)})
    data = await ask_json(deps, budget, messages)
    answer = _parse(data, job)
    if answer.status == "invalid" and budget.can_call():
        data = await ask_json(deps, budget, _repair(messages, data, ["the answer was not the JSON object described"]))
        answer = _parse(data, job)
    if answer.status in ("needs_info", "off_topic", "private_message", "publish"):
        return Composed(answer.status)
    if answer.status != "ok":
        return Composed("failed")
    return await _checked(deps, budget, job, messages, data, answer)


async def _checked(deps: TurnDeps, budget: Budget, job: Job, messages: tuple, data: dict,
                   answer: Answer) -> Composed:
    basis = Basis(Ground.of(job.ground), Ground.of((*job.ground, *job.org)), job.voice)
    variants = [prepare(v, job.voice) for v in answer.variants]
    found = problems(variants, basis, len(job.platforms))
    repaired = False
    if found and budget.can_call():
        log.info("repairing variants: %d problem(s)", len(found))
        again = _parse(await ask_json(deps, budget, _repair(messages, data, found)), job)
        if again.status == "ok":
            variants, repaired = [prepare(v, job.voice) for v in again.variants], True
    if job.current:
        variants = [_keep_facts(v, old) for v, old in zip(variants, job.current, strict=False)]
    final, notes = finalize(variants, basis, drop_factless=not job.current)
    return Composed("ok", final, notes, repaired, answer.goal)


def _keep_facts(variant: Variant, old: Variant) -> Variant:
    return variant if variant.facts else replace(variant, facts=old.facts)


def _parse(data: dict | None, job: Job) -> Answer:
    return parse_answer(data, tuple(a.name for a in job.angles), job.platforms, GOALS)


def _user(job: Job) -> str:
    current = None
    if job.current:
        pairs = zip(job.current, job.platforms, strict=False)
        current = "\n\n".join(_render_current(n, v, p) for n, (v, p) in enumerate(pairs, start=1))
    targets = "each variant" if len(job.current) != 1 else "the variant"
    return prompts.draft_user(job.request, job.material, job.facts, job.platforms, job.angles, job.goal,
                              job.voice.descriptors(), current, targets)


def _render_current(number: int, variant: Variant, target: str) -> str:
    body = "\n---\n".join(variant.posts)
    return f"Variant {number} ({variant.angle}; write it as: {target}):\n{body}"


def _repair(messages: tuple, data: dict | None, violations: list[str]) -> tuple:
    previous = json.dumps(data, ensure_ascii=False) if data is not None else "(not a JSON object)"
    return (*messages, {"role": "user", "content": prompts.repair_user(previous, violations)})


async def read_voice(deps: TurnDeps, budget: Budget, sample: str) -> Voice | None:
    messages = ({"role": "system", "content": prompts.VOICE_SYSTEM},
                {"role": "user", "content": prompts.voice_user(sample)})
    return from_sample(await ask_json(deps, budget, messages))
