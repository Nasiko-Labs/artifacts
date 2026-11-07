"""The model steps: write or revise a reply (one call, at most one repair, then code fixes)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace

import prompts
import style
import tone_check
from checks import Basis, Fixed, fix, kept_asks, problems, spans
from draft import Answer, Reply, parse_answer
from grounding import Ground
from incoming import Incoming
from llm import Budget, TurnDeps, ask_json
from register import register_for
from render import sanitize

log = logging.getLogger("reply_drafter.drafting")


@dataclass(frozen=True)
class Job:
    incoming: Incoming | None
    instruction: str                    # the purpose (new reply) or the revision instruction
    words: tuple[str, ...]              # the user's own words for this reply: they ground facts and links
    org: tuple[str, ...]
    channel: str
    metrics: style.Metrics
    current: Reply | None = None        # set for a revision
    relaxed: bool = False
    short: bool = False
    want_subject: bool = False


@dataclass(frozen=True)
class Composed:
    status: str                         # ok | needs_info | off_topic | failed
    reply: Reply | None = None
    missing: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    code_fixed: bool = False            # problems remained after the repair and code fixed them
    ungrounded_after_repair: bool = False


def target(job: Job) -> tone_check.Target:
    sender = job.incoming.sender if job.incoming else ""
    return tone_check.Target(job.metrics, register_for(job.channel), sender, job.relaxed, job.short)


def basis(job: Job) -> Basis:
    incoming = job.incoming.text if job.incoming else ""
    tgt = target(job)
    allowed = (tone_check.expected_greeting(tgt), job.metrics.sign_off, tgt.sender)
    return Basis(Ground.of((incoming, *job.words)), Ground.of(job.words), Ground.of(allowed), tgt, incoming)


def _style_lines(job: Job) -> tuple[str, str, str, str]:
    tgt, reg = target(job), register_for(job.channel)
    greeting = tone_check.expected_greeting(tgt) if reg.greeting and job.metrics.greeting != style.NONE else ""
    sign_off = job.metrics.sign_off.replace("\n", " / ") if reg.sign_off and job.metrics.sign_off != style.NONE else ""
    register = f"{reg.label}: {reg.guidance} Cap: {reg.max_words} words."
    return style.descriptors(job.metrics, job.short), register, greeting, sign_off


def _messages(deps: TurnDeps, job: Job) -> tuple:
    descr, register, greeting, sign_off = _style_lines(job)
    sender = job.incoming.sender if job.incoming else ""
    if job.current is not None and job.incoming is not None:
        user = prompts.revise_user(job.incoming.text, job.instruction, job.current.draft, descr, register, sender,
                                   greeting, sign_off)
    else:
        user = prompts.draft_user(job.incoming.text if job.incoming else None, job.instruction, descr, register,
                                  sender, greeting, sign_off)
    return ({"role": "system", "content": prompts.system(deps.today, job.org)}, {"role": "user", "content": user})


async def compose(deps: TurnDeps, budget: Budget, job: Job) -> Composed:
    messages = _messages(deps, job)
    data = await ask_json(deps, budget, messages)
    answer = parse_answer(data)
    if answer.status == "invalid" and budget.can_call():
        data = await ask_json(deps, budget, _repair(messages, data, ["the answer was not the JSON object described"]))
        answer = parse_answer(data)
    if answer.status == "off_topic":
        return Composed("off_topic")
    if answer.status == "needs_info" or job.incoming is None:
        return Composed("needs_info", missing=answer.missing or ("incoming",))
    if answer.status != "ok" or answer.reply is None:
        return Composed("failed")
    return await _checked(deps, budget, job, messages, data, answer)


async def _checked(deps: TurnDeps, budget: Budget, job: Job, messages: tuple, data: dict, answer: Answer) -> Composed:
    b = basis(job)
    reply = _clean(answer.reply, job)
    found = problems(reply, b)
    if found and budget.can_call():
        log.info("repairing reply: %d problem(s)", len(found))
        repaired = parse_answer(await ask_json(deps, budget, _repair(messages, data, found)))
        if repaired.status == "ok" and repaired.reply is not None:
            reply = _clean(repaired.reply, job)
            found = problems(reply, b)
    left = bool(spans(reply, b))
    fixed = fix(reply, b) if found else Fixed(reply, ())
    final = replace(fixed.reply, asks=kept_asks(fixed.reply.asks, b.incoming))
    return Composed("ok", reply=final, notes=fixed.notes, code_fixed=bool(found), ungrounded_after_repair=left)


def _clean(reply: Reply, job: Job) -> Reply:
    """Rendered-safe text, and the subject set by code: `Re: <incoming subject>` on email, none elsewhere."""
    reg = register_for(job.channel)
    subject = ""
    if reg.subject:
        incoming_subject = job.incoming.reply_subject if job.incoming else ""
        subject = incoming_subject or sanitize(reply.subject)
    alt = sanitize(reply.alt_shorter) if reply.alt_shorter else None
    return replace(reply, subject=subject, draft=sanitize(reply.draft), alt_shorter=alt or None)


def _repair(messages: tuple, data: dict | None, violations: list[str]) -> tuple:
    previous = json.dumps(data, ensure_ascii=False) if data is not None else "(not a JSON object)"
    return (*messages, {"role": "user", "content": prompts.repair_user(previous, violations)})
