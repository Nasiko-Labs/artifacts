"""The model steps: write or revise a draft (one call, at most one repair, then code fixes), and read a
tone profile from a writing sample (one call)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace

import prompts
from checks import Basis, fix, problems, strip_emoji, verified_facts
from draft import Answer, Draft, parse_answer
from grounding import Ground
from llm import Budget, TurnDeps, ask_json
from register import register_for
from render import sanitize
from tone import Profile, from_sample

log = logging.getLogger("drafter.drafting")


@dataclass(frozen=True)
class Job:
    request: str                        # the user's words (first paragraph)
    context: str                        # pasted context or later paragraphs (data)
    given: tuple[str, ...]              # pause answers ("recipient: my manager")
    org: tuple[str, ...]                # organization instructions
    channel: str | None                 # named by the user (wins over the model)
    length: str
    profile: Profile
    current: Draft | None
    ground_new: tuple[str, ...]         # grounds a new message
    ground_revision: tuple[str, ...]    # grounds a revision: earlier facts plus this message
    addressing_new: tuple[str, ...]
    addressing_revision: tuple[str, ...]
    known: frozenset[str]               # slots already answered


@dataclass(frozen=True)
class Composed:
    status: str                         # ok | needs_info | off_topic | failed
    draft: Draft | None = None
    missing: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    ground: tuple[str, ...] = ()
    addressing: tuple[str, ...] = ()


async def compose(deps: TurnDeps, budget: Budget, job: Job) -> Composed:
    current = _render_current(job.current) if job.current else None
    reg = register_for(job.channel or (job.current.channel if job.current else "email"))
    user = prompts.compose_user(job.request, job.context, job.given, job.channel,
                                f"{reg.label}: {reg.guidance} Length cap: {reg.cap(job.length)} {reg.unit}.",
                                job.profile.descriptors(), current)
    messages = ({"role": "system", "content": prompts.system(deps.today, job.org)}, {"role": "user", "content": user})
    data = await ask_json(deps, budget, messages)
    answer = parse_answer(data, job.channel)
    if answer.status == "invalid" and budget.can_call():
        data = await ask_json(deps, budget, _repair(messages, data, ["the answer was not the JSON object described"]))
        answer = parse_answer(data, job.channel)
    if answer.status == "off_topic":
        return Composed("off_topic")
    if answer.status == "needs_info":
        missing = tuple(s for s in answer.missing if s not in job.known)
        return Composed("needs_info", missing=missing) if missing else Composed("failed")
    if answer.status != "ok":
        return Composed("failed")
    return await _checked(deps, budget, job, messages, data, answer)


async def _checked(deps: TurnDeps, budget: Budget, job: Job, messages: tuple, data: dict, answer: Answer) -> Composed:
    revision = job.current is not None and answer.kind == "revision"
    ground = job.ground_revision if revision else job.ground_new
    addressing = job.addressing_revision if revision else job.addressing_new
    basis = Basis(Ground.of(ground), Ground.of(addressing), job.profile, job.length)
    draft = _clean(answer.draft, job.profile)
    found = problems(draft, basis)
    if found and budget.can_call():
        log.info("repairing draft: %d problem(s)", len(found))
        repaired = parse_answer(await ask_json(deps, budget, _repair(messages, data, found)), job.channel)
        if repaired.status == "ok":
            draft = _clean(repaired.draft, job.profile)
            found = problems(draft, basis)
    notes: tuple[str, ...] = ()
    if found:
        draft, notes = fix(draft, basis)
    return Composed("ok", draft=verified_facts(draft, basis.ground), notes=notes, ground=ground,
                    addressing=addressing)


def _clean(draft: Draft, profile: Profile) -> Draft:
    return strip_emoji(replace(draft, body=sanitize(draft.body), subject=sanitize(draft.subject)), profile)


def _repair(messages: tuple, data: dict | None, violations: list[str]) -> tuple:
    previous = json.dumps(data, ensure_ascii=False) if data is not None else "(not a JSON object)"
    return (*messages, {"role": "user", "content": prompts.repair_user(previous, violations)})


def _render_current(draft: Draft) -> str:
    who = ", ".join(p for p in (draft.recipient_name, draft.recipient_role) if p) or "unknown"
    subject = f"Subject: {draft.subject}\n" if draft.subject else ""
    return f"To: {who}\nChannel: {draft.channel}\n{subject}\n{draft.body}"


async def read_tone(deps: TurnDeps, budget: Budget, sample: str) -> Profile | None:
    messages = ({"role": "system", "content": prompts.TONE_SYSTEM},
                {"role": "user", "content": prompts.tone_user(sample)})
    data = await ask_json(deps, budget, messages)
    return from_sample(data, sample) if data is not None else None
