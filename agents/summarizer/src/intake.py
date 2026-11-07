"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                         → canned intro (no call)
    ≥ 60 words of source  ─┬─ over the size cap   → pause: first part or cancel
                           ├─ refers back to a stored text → pause: on its own or together
                           └─ otherwise           → summarize
    short, a summary stored ─┬─ re-render command → render (no call)
                             └─ anything else     → one quote-checked question
    short, nothing stored ─┬─ "summarize this" with no text → pause: paste the text
                           ├─ a question or task  → one scope-checked call (declined or "no text yet")
                           └─ otherwise           → "short enough to read as is" (no call)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import hitl
import prompts
import smalltalk
from inputs import (
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    assemble_source,
    split_request,
    truncate_source,
    word_count,
)

SHORT_REQUEST_MAX_WORDS = 12
SHORT_SUMMARIZE_MAX_WORDS = 4
TOO_BIG_TO_COMBINE = ("The earlier text and this one together are over my size limit, so I summarized this one "
                      "on its own.")
CUT_TO_CAP = "That was over my size limit, so I summarized the first ~20k words."
_REFERS_BACK = re.compile(r"\b(?:too|also|as well|together|combined?|along with|plus this|add(?:ed)? to|"
                          r"with the (?:earlier|previous|last|first|other) (?:one|text|summary|thread|call)|"
                          r"and this one)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                              # reply | pause | summarize | together | rerender | ask
    reply: str = ""
    pending: hitl.Pending | None = None
    source: str = ""
    instruction: str = ""
    view: tuple[str, bool] = ("default", False)
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, stored_source: str) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=incoming.notes)
    instruction, pasted = split_request(incoming.text)
    source = prompts.strip_markers(assemble_source(pasted, incoming.files))
    words = word_count(source)
    if words >= MIN_SOURCE_WORDS:
        return _new_source(source, prompts.strip_markers(instruction), words, stored_source, incoming.notes)
    if stored_source:
        return _on_stored(incoming)
    if followup.is_summarize_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        pending = hitl.Pending(hitl.NEED_SOURCE, instruction=prompts.strip_markers(incoming.text)[:300],
                               notes=incoming.notes)
        return Plan("pause", pending=pending)
    if followup.is_question_like(incoming.text):
        return Plan("ask", notes=incoming.notes)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=incoming.notes)


def plan_resume(answer: hitl.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    source = prompts.strip_markers(answer.source)
    notes: tuple[str, ...] = ()
    if len(source) > MAX_SOURCE_CHARS:
        source, notes = truncate_source(source), (CUT_TO_CAP,)
    route = "together" if answer.action == "together" else "summarize"
    return Plan(route, source=source, instruction=answer.instruction, notes=notes)


def _new_source(source: str, instruction: str, words: int, stored_source: str, notes: tuple[str, ...]) -> Plan:
    if len(source) > MAX_SOURCE_CHARS:
        pending = hitl.Pending(hitl.TOO_BIG, instruction=instruction, source=truncate_source(source), words=words,
                               notes=notes)
        return Plan("pause", pending=pending)
    if stored_source and instruction and _REFERS_BACK.search(instruction):
        if len(stored_source) + 2 + len(source) > MAX_SOURCE_CHARS:
            return Plan("summarize", source=source, instruction=instruction, notes=(*notes, TOO_BIG_TO_COMBINE))
        pending = hitl.Pending(hitl.COMBINE, instruction=instruction, source=source, words=words, can_combine=True,
                               notes=notes)
        return Plan("pause", pending=pending)
    return Plan("summarize", source=source, instruction=instruction, notes=notes)


def _on_stored(incoming: Incoming) -> Plan:
    command = followup.parse_command(incoming.text)
    if command is not None:
        return Plan("rerender", view=command, notes=incoming.notes)
    if followup.is_summarize_request(incoming.text) and word_count(incoming.text) <= SHORT_SUMMARIZE_MAX_WORDS:
        return Plan("rerender", notes=incoming.notes)
    return Plan("ask", notes=incoming.notes)
