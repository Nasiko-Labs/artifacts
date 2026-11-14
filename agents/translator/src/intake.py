"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                              → canned intro (no call)
    instruction + text, or an envelope                 → translate (or pause: no language, too big)
    a natural request ("how do you say X in German")   → translate (`natural.parse`, no call)
    short follow-up on the stored translation          → original / json (no call) or re-translate
    an instruction with no text                        → pause: paste the text
    anything else                                      → one scope call: the text (and language) to translate,
                                                         or declined
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import followup
import hitl
import natural
import prompts
import request
import smalltalk
from inputs import Incoming
from request import Job

TOO_BIG_ENVELOPE = "That agent output is over my size limit (about 60,000 characters), so I can't translate it."


@dataclass(frozen=True)
class Plan:
    route: str                      # reply | pause | translate | scope | original | json
    reply: str = ""
    pending: hitl.Pending | None = None
    job: Job | None = None
    as_json: bool = False


def plan_turn(incoming: Incoming, stored: Job | None) -> Plan:
    if incoming.data is None:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            return Plan("reply", canned)
    text = prompts.strip_markers(incoming.text)
    instruction, body = request.split(text)
    envelope = _envelope(incoming.data) or request.envelope_from(body)
    base = carry(stored, incoming.as_json)
    if envelope or (instruction and body):
        job = replace(base, source="" if envelope else body, envelope=envelope)
        return next_step(request.apply_instruction(job, instruction))
    follow = followup.parse(text, stored)
    if follow is not None:
        job = replace(follow.job, as_json=incoming.as_json) if follow.job else None
        return Plan(follow.action, job=job, as_json=incoming.as_json or follow.action == "json")
    spoken = natural.parse(text)  # "how do you say thank you in German", "translate I am hungry into spanish"
    if spoken is not None:
        return next_step(request.apply_instruction(replace(base, source=spoken[1]), spoken[0]))
    if instruction:  # a cue with no text
        return next_step(request.apply_instruction(base, instruction))
    return Plan("scope", job=replace(base, source=body))


def from_extraction(job: Job, message: str, extraction: natural.Extraction) -> Job:
    """The scope call's extraction applied to `job`: the checked text, and the named target (a name that is
    not a known language clears the target and is reported as unknown)."""
    source, target, unknown = natural.checked_extraction(extraction, message)
    if target is not None:
        return replace(job, source=source, target=target.language.code, target_note=target.note, unknown="")
    if unknown:
        return replace(job, source=source, target="", target_note="", unknown=unknown)
    return replace(job, source=source)


def carry(stored: Job | None, as_json: bool) -> Job:
    """A new job keeps the conversation's target, register and glossary, never its text."""
    if stored is None:
        return Job(as_json=as_json)
    return Job(target=stored.target, register=stored.register, keep=stored.keep, glossary=stored.glossary,
               as_json=as_json)


def next_step(job: Job) -> Plan:
    if not job.has_text:
        return Plan("pause", pending=hitl.Pending(hitl.NEED_TEXT, job))
    if job.envelope and len(json.dumps(job.envelope, ensure_ascii=False)) > hitl.MAX_SOURCE_CHARS:
        return Plan("reply", TOO_BIG_ENVELOPE)
    if len(job.source) > hitl.MAX_SOURCE_CHARS:
        return Plan("pause", pending=hitl.Pending(hitl.TOO_BIG, replace(job, source=hitl.cut(job.source))))
    if not job.target:
        return Plan("pause", pending=hitl.Pending(hitl.NEED_LANGUAGE, job))
    return Plan("translate", job=job)


def plan_resume(answer: hitl.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "reply":
        return Plan("reply", answer.reply)
    return next_step(answer.job)


def _envelope(data: dict | None) -> dict | None:
    if not data:
        return None
    return request.envelope_from(json.dumps(data, ensure_ascii=False))
