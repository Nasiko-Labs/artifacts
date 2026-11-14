"""Pure: decide what one turn does, before any LLM call. Nothing is stored between messages and nothing
ever pauses: every route ends the task `completed`. The last job comes only from the platform's history
block (`recall.replay`).

    greeting / small talk                         -> canned intro (no call)
    instruction + text, or an envelope            -> translate (over the cap: the first part, said so)
    a natural request ("how do you say X in German") -> translate (`natural.parse`, no call)
    short follow-up on the last job               -> original (no call) or re-translate / json (one run)
      ... whose text was trimmed from the history -> translate the last translation into the new language
                                                     (said so), else ask for a re-paste
    an instruction with no text                   -> reply: paste the text (completed)
    text with no target language                  -> reply: which language? (completed; the answer works
                                                     from the history)
    anything else                                 -> one scope call: the text (and language) to translate,
                                                     or declined
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import followup
import languages
import natural
import prompts
import recall
import render
import request
import smalltalk
from inputs import Incoming
from request import MAX_SOURCE_CHARS, Job

TOO_BIG_ENVELOPE = "That agent output is over my size limit (about 60,000 characters), so I can't translate it."


@dataclass(frozen=True)
class Plan:
    route: str                      # reply | translate | scope
    reply: str = ""
    job: Job | None = None
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming) -> Plan:
    if incoming.data is None:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            return Plan("reply", canned)
    stored = recall.replay(incoming.history)
    text = prompts.strip_markers(incoming.text)
    instruction, body = request.split(text)
    envelope = _envelope(incoming.data) or request.envelope_from(body)
    base = recall.carry(stored, incoming.as_json)
    if envelope or (instruction and body):
        job = replace(base, source="" if envelope else body, envelope=envelope)
        return next_step(request.apply_instruction(job, instruction), incoming)
    follow = followup.parse(text, stored)
    if follow is not None:
        return _follow_up(follow, stored, incoming)
    spoken = natural.parse(text)  # "how do you say thank you in German", "translate I am hungry into spanish"
    if spoken is not None:
        return next_step(request.apply_instruction(replace(base, source=spoken[1]), spoken[0]), incoming)
    if instruction:  # a cue with no text
        return next_step(request.apply_instruction(base, instruction), incoming)
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


def _follow_up(follow: followup.FollowUp, stored: Job | None, incoming: Incoming) -> Plan:
    if follow.action == "original":
        has_text = stored is not None and stored.has_text
        return Plan("reply", render.original(stored.source, stored.envelope) if has_text else prompts.NOTHING_STORED)
    if follow.action == "json":
        if stored is None or not stored.has_text:
            return Plan("reply", prompts.NOTHING_STORED)
        return next_step(replace(stored, as_json=True), incoming)
    return next_step(replace(follow.job, as_json=incoming.as_json or follow.job.as_json), incoming, is_follow_up=True)


def next_step(job: Job, incoming: Incoming, is_follow_up: bool = False) -> Plan:
    if not job.has_text:
        return _without_text(job, incoming, is_follow_up)
    if job.envelope and len(json.dumps(job.envelope, ensure_ascii=False)) > MAX_SOURCE_CHARS:
        return Plan("reply", TOO_BIG_ENVELOPE)
    if not job.target:
        lead = prompts.UNSUPPORTED.format(name=job.unknown[:40], names=", ".join(languages.supported_names()))
        return Plan("reply", prompts.NEED_LANGUAGE.format(lead=lead if job.unknown else ""))
    if len(job.source) > MAX_SOURCE_CHARS:
        kept = request.cut(job.source)
        note = prompts.TOO_BIG_NOTE.format(total=len(job.source), limit=MAX_SOURCE_CHARS, used=len(kept),
                                           left=len(job.source) - len(kept))
        return Plan("translate", job=replace(job, source=kept), notes=(note,))
    return Plan("translate", job=job)


def _without_text(job: Job, incoming: Incoming, is_follow_up: bool) -> Plan:
    """No text in this message or the history: translate the last translation into a NEW language when one is
    still visible (said so), else ask for the text."""
    earlier = recall.last_translation(incoming.history)
    if earlier is not None and job.target and earlier[1].casefold() != job.target.casefold():
        language = languages.BY_CODE[earlier[1].casefold()].name
        note = prompts.FROM_TRANSLATION_NOTE.format(language=language)
        return Plan("translate", job=replace(job, source=earlier[0], source_hint=language), notes=(note,))
    if is_follow_up and incoming.history:
        return Plan("reply", prompts.NOTHING_STORED)
    into = f" into {languages.BY_CODE[job.target.casefold()].name}" if job.target else ""
    return Plan("reply", prompts.NEED_TEXT.format(into=into))


def _envelope(data: dict | None) -> dict | None:
    if not data:
        return None
    return request.envelope_from(json.dumps(data, ensure_ascii=False))
