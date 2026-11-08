"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                          -> canned intro (no call)
    "I'm Raj"                                      -> remember the name (no call; re-render if stored)
    >= 80 words of transcript ─┬─ over the size cap -> pause: first part, last part or cancel
                               └─ otherwise         -> process
    short, a meeting stored ─┬─ view command        -> render (no call)
                             └─ anything else       -> one quote-checked question
    short, nothing stored ─┬─ "process this" with no text -> pause: paste the transcript
                           ├─ a question or task    -> one scope-checked call
                           └─ otherwise             -> "too short" (no call)
"""

from __future__ import annotations

from dataclasses import dataclass

import followup
import hitl
import prompts
import smalltalk
from inputs import (
    MAX_SOURCE_CHARS,
    MIN_SOURCE_WORDS,
    Incoming,
    cut,
    from_envelope,
    my_name_in,
    name_only,
    split_request,
    word_count,
)

SHORT_REQUEST_MAX_WORDS = 12


@dataclass(frozen=True)
class Plan:
    route: str                     # reply | pause | process | rerender | ask | set_name
    reply: str = ""
    pending: hitl.Pending | None = None
    source: str = ""
    instruction: str = ""
    my_name: str = ""
    view: str = "default"
    wants_json: bool = False
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_stored: bool) -> Plan:
    wants, notes = incoming.wants_json, incoming.notes
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, notes=notes)
    name = "" if incoming.files else name_only(incoming.text)
    if name:
        return Plan("set_name", my_name=name, notes=notes)
    instruction, source = source_of(incoming)
    words = word_count(source)
    if words >= MIN_SOURCE_WORDS:
        my_name = my_name_in(instruction)
        if len(source) > MAX_SOURCE_CHARS:
            pending = hitl.Pending(hitl.TOO_BIG, instruction=instruction, my_name=my_name, head=cut(source, False),
                                   tail=cut(source, True), words=words, wants_json=wants)
            return Plan("pause", pending=pending, notes=notes)
        return Plan("process", source=source, instruction=instruction, my_name=my_name, wants_json=wants, notes=notes)
    if has_stored:
        view = followup.parse_command(incoming.text)
        if view is not None:
            return Plan("rerender", view=view, wants_json=wants or view == "json", notes=notes)
        return Plan("ask", notes=notes)
    if followup.is_process_request(incoming.text) and word_count(incoming.text) <= SHORT_REQUEST_MAX_WORDS:
        text = prompts.strip_markers(incoming.text)[:300]
        pending = hitl.Pending(hitl.NEED_SOURCE, instruction=text, my_name=my_name_in(text), wants_json=wants)
        return Plan("pause", pending=pending, notes=notes)
    if followup.is_question_like(incoming.text):
        return Plan("ask", notes=notes)
    return Plan("reply", smalltalk.TOO_SHORT_REPLY, notes=notes)


def plan_resume(pending: hitl.Pending, answer: hitl.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    source = prompts.strip_markers(answer.source)
    notes = (answer.note,) if answer.note else ()
    if len(source) > MAX_SOURCE_CHARS:  # a pasted answer over the cap: the first part, said so
        source, notes = cut(source, False), ("That was over my size limit, so I processed the first ~20k words.",)
    return Plan("process", source=source, instruction=pending.instruction, my_name=pending.my_name,
                wants_json=pending.wants_json, notes=notes)


def source_of(incoming: Incoming) -> tuple[str, str]:
    """(instruction, transcript) with fence markers stripped from both."""
    enveloped = from_envelope(incoming.text)
    if enveloped is not None:
        return "", prompts.strip_markers(enveloped)
    if incoming.files:
        files = "\n\n".join(text.strip() for _, text in incoming.files if text.strip())
        return prompts.strip_markers(incoming.text.strip())[:300], prompts.strip_markers(files)
    instruction, source = split_request(incoming.text)
    return prompts.strip_markers(instruction), prompts.strip_markers(source)
