"""Pure: decides what a turn is: small talk, a follow-up on the stored runbook, a new runbook, a pause for the
missing source, or one classify call for a short message that is none of these."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import followup
import hitl
import mode
import prompts
import smalltalk
from inputs import (
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    split_request,
    word_count,
)

_DOMAIN = re.compile(r"\brun ?books?\b|\bplaybooks?\b|\bon-?call\b|\bincident\b|\boutage\b|\bservice\b|\brollback\b|"
                     r"\bmitigat\w*|\btriage\b|\bpostmortem\b|\bpost-mortem\b|\bsre\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | pause | run | followup | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    source: str = ""
    instruction: str = ""
    mode: str = ""
    command: followup.Command | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def plan_turn(incoming: Incoming, has_runbook: bool) -> Route:
    text, notes = incoming.text, incoming.notes
    if not incoming.files:
        canned = smalltalk.reply_to(text)
        if canned is not None:
            return Route("reply", reply=_with(canned, notes))
        command = followup.parse(text)
        if command is not None:
            if has_runbook:
                return Route("followup", command=command, notes=notes)
            if command.kind != "fill":  # "fill" may just be prose; let it fall through
                return Route("reply", reply=_with(smalltalk.NO_RUNBOOK_YET, notes))
    return plan_source(text, incoming, notes)


def plan_source(text: str, incoming: Incoming, notes: tuple[str, ...], instruction: str = "") -> Route:
    request = split_request(text, incoming.files)
    source = prompts.strip_markers(request.source)
    if len(source) > MAX_SOURCE_CHARS:
        cut = (f"Your text was {len(source):,} characters; I covered the first {MAX_SOURCE_CHARS:,} and "
               "skipped the rest.")
        notes = (*notes, cut)
        source = source[:MAX_SOURCE_CHARS]
    if word_count(source) >= MIN_SOURCE_WORDS:
        forced = mode.TRANSCRIPT if request.from_envelope else None
        asks = [a for a in (instruction, request.instruction) if a]
        return Route("run", source=source, instruction="\n".join(dict.fromkeys(asks)),
                     mode=mode.detect(source, forced), notes=notes)
    if _DOMAIN.search(text):
        return Route("pause", pending=hitl.need_source(text, incoming.wants_json), notes=notes)
    return Route("scope", notes=notes)


def plan_resume(answer: hitl.Answer, pending: hitl.Pending, incoming: Incoming) -> Route:
    if answer.action == "run":
        return plan_source(answer.text, Incoming(answer.text, incoming.policy, (), (), pending.wants_json), (),
                           pending.instruction)
    if answer.action == "reask":
        return Route("pause", pending=answer.pending)
    if answer.action == "give_up":
        return Route("reply", reply=prompts.NO_SOURCE_GIVEN_UP)
    return Route("reply", reply=prompts.CANCELLED)


def _with(text: str, notes: tuple[str, ...]) -> str:
    return "\n\n".join(p for p in (text, *notes) if p)
