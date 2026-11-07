"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                              → canned intro (no call)
    pasted text (≥ 15 words) ─┬─ over the size cap     → pause: first part, last part, cancel
                              └─ otherwise             → scan (merged into the stored list)
    short, a list stored ─┬─ re-render command         → render (no call)
                          └─ anything else             → one quote-checked question
    short, nothing stored ─┬─ "find my promises" alone → pause: paste the text
                           ├─ a question or task       → one scope-checked call (declined or "no text yet")
                           └─ otherwise                → "too short to scan" (no call)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import dates
import followup
import hitl
import prompts
import smalltalk
from inputs import (
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    head_of,
    split_request,
    tail_of,
    word_count,
)

AS_OF_LOOK_CHARS = 300
SHORT_REQUEST_MAX_WORDS = 20
CUT_HEAD = "That was over my size limit, so I scanned the first ~20k words."
CUT_TAIL = "That was over my size limit, so I scanned the last ~20k words."


@dataclass(frozen=True)
class Plan:
    route: str                                  # reply | pause | scan | rerender | ask
    reply: str = ""
    pending: hitl.Pending | None = None
    source: str = ""
    instruction: str = ""
    as_of: date | None = None                   # an explicit as-of in this turn's words
    command: followup.Command | None = None
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, stored: bool, recipients: Sequence[str], today: date) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    request = split_request(incoming.text, incoming.files)
    source = prompts.strip_markers(request.source)
    instruction = prompts.strip_markers(request.instruction)[:AS_OF_LOOK_CHARS]
    as_of = dates.parse_as_of(instruction or incoming.text[:AS_OF_LOOK_CHARS], today)
    if _is_paste(incoming, source, stored):
        if len(source) > MAX_SOURCE_CHARS:
            pending = hitl.Pending(hitl.TOO_BIG, instruction=instruction, head=head_of(source), tail=tail_of(source),
                                   notes=incoming.notes)
            return Plan("pause", pending=pending)
        return Plan("scan", source=source, instruction=instruction, as_of=as_of, notes=incoming.notes)
    if stored:
        command = followup.parse_command(incoming.text, recipients, today)
        if command is not None:
            return Plan("rerender", command=command, notes=incoming.notes)
        return Plan("ask", notes=incoming.notes)
    if followup.is_scan_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        pending = hitl.Pending(hitl.NEED_SOURCE, instruction=prompts.strip_markers(incoming.text)[:AS_OF_LOOK_CHARS],
                               notes=incoming.notes)
        return Plan("pause", pending=pending)
    if followup.is_question_like(incoming.text):
        return Plan("ask", notes=incoming.notes)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)


def _is_paste(incoming: Incoming, source: str, stored: bool) -> bool:
    if incoming.files and source.strip():
        return True
    if word_count(source) < MIN_SOURCE_WORDS:
        return False
    one_line_question = "\n" not in source.strip() and followup.is_question_like(source)
    return not (stored and one_line_question)


def plan_resume(answer: hitl.Answer, today: date) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    source = prompts.strip_markers(answer.source)
    notes: tuple[str, ...] = ()
    if len(source) > MAX_SOURCE_CHARS:
        source, notes = head_of(source), (CUT_HEAD,)
    as_of = dates.parse_as_of(answer.instruction, today)
    return Plan("scan", source=source, instruction=answer.instruction, as_of=as_of, notes=notes)


def cut_note(pending: hitl.Pending, answer: hitl.Answer) -> tuple[str, ...]:
    if pending.kind != hitl.TOO_BIG or answer.action != "run":
        return ()
    return (CUT_TAIL,) if answer.source == pending.tail and pending.tail != pending.head else (CUT_HEAD,)
