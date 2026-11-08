"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk / a bare sum             → canned reply (no call)
    another agent's envelope                       → humanize its draft fields
    a draft of ≥ 15 words ─┬─ over 20,000 chars    → pause: first ~3k words or a shorter section
                           └─ otherwise            → humanize
    a draft is stored ─┬─ show the changes / patterns / as json → render (no call)
                       └─ an option ("keep my em dashes", "more casual") → re-run from the stored original
    a humanize request with no draft               → pause: paste the draft
    anything else                                  → one scope call (declined, or asked for the draft)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import envelope
import hitl
import options
import prompts
import smalltalk
from inputs import (
    CUE,
    MAX_DRAFT_CHARS,
    MIN_DRAFT_WORDS,
    Incoming,
    split_draft,
    word_count,
)

COMMAND_MAX_WORDS = 12
CUE_REQUEST_MAX_WORDS = 25
_CHANGES = re.compile(r"\bchanges?\b|\bwhat (?:did you|was) change(?:d)?\b|\bdiff\b")
_PATTERNS = re.compile(r"\bpatterns?\b|\bwhat did you (?:remove|find)\b")


@dataclass(frozen=True)
class Plan:
    route: str                     # reply | pause | humanize | envelope | rerun | show | classify
    reply: str = ""
    pending: hitl.Pending | None = None
    draft: str = ""
    request: str = ""
    envelope: dict | None = None
    command: str = ""              # show: changes | patterns | json
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, stored: bool) -> Plan:
    text = incoming.text
    if not incoming.files:
        canned = smalltalk.reply_to(text)
        if canned is not None:
            return Plan("reply", canned, notes=incoming.notes)
        if smalltalk.is_arithmetic(text):
            return Plan("reply", smalltalk.OFF_TOPIC_REPLY)
    parsed_envelope = envelope.parse(text)
    if parsed_envelope is not None:
        data, request = parsed_envelope
        return Plan("envelope", envelope=data, request=prompts.strip_markers(request), notes=incoming.notes)
    request, draft = split_draft(text)
    if incoming.files:
        request, draft = text, "\n\n".join(body.strip() for _, body in incoming.files)
    draft, request = prompts.strip_markers(draft).strip(), prompts.strip_markers(request)
    if word_count(draft) >= MIN_DRAFT_WORDS:
        if len(draft) > MAX_DRAFT_CHARS:
            return Plan("pause", pending=hitl.too_big(request, draft))
        return Plan("humanize", draft=draft, request=request, notes=incoming.notes)
    if stored and word_count(text) <= COMMAND_MAX_WORDS:
        stored_plan = _on_stored(text)
        if stored_plan is not None:
            return stored_plan
    if CUE.search(text) and word_count(text) <= CUE_REQUEST_MAX_WORDS:
        return Plan("pause", pending=hitl.need_draft(text))
    return Plan("classify", request=prompts.strip_markers(text), notes=incoming.notes)


def _on_stored(text: str) -> Plan | None:
    plain = " ".join(text.casefold().split())
    parsed = options.parse(text)
    if parsed.changed:
        return Plan("rerun", request=text)
    if _PATTERNS.search(plain):
        return Plan("show", command="patterns")
    if _CHANGES.search(plain):
        return Plan("show", command="changes")
    if parsed.as_json:
        return Plan("show", command="json")
    return None


def plan_resume(answer: hitl.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_DRAFT_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    return Plan("humanize", draft=answer.draft, request=answer.request, notes=answer.notes)
