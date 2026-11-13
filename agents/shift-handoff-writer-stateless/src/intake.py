"""Pure: decide what one turn does, before any LLM call. Nothing is stored between turns: earlier work is
whatever `recall.Prior` finds in the platform's chat history. No turn ever pauses.

    greeting / small talk                              -> canned intro (no call)
    >= 40 words of notes ─┬─ over 100,000 chars        -> build from the most recent part, say what was cut
                          └─ otherwise                 -> build the handoff (with the earlier request's
                                                          instruction when our last reply asked for notes)
    a handoff recalled ─┬─ a follow-up command         -> rebuild it, then view / json / mark / add note
                        └─ anything else               -> one scope-check call (decline or the follow-up help)
    nothing recalled ─┬─ "tell / send / post ..."      -> "I don't act" (no call)
                      ├─ a follow-up, history sent     -> "paste the notes again" (trimmed)
                      ├─ "write my handoff", no notes  -> reply asking for the notes (no call)
                      └─ anything else                 -> one scope-check call (decline or "short enough")
"""

from __future__ import annotations

from dataclasses import dataclass

import followup
import prompts
import smalltalk
from entries import split_instruction
from inputs import (
    MAX_NOTES_CHARS,
    MIN_NOTES_WORDS,
    Incoming,
    assemble,
    from_envelope,
    most_recent,
    word_count,
)
from recall import Prior

SHORT_ASK_MAX_WORDS = 12


@dataclass(frozen=True)
class Plan:
    route: str                                  # reply | build | followup | scope
    reply: str = ""
    notes: str = ""
    instruction: str = ""
    command: followup.Command | None = None
    has_stored: bool = False
    extra: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, prior: Prior) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, extra=incoming.notes)
    any_command = followup.parse_command(incoming.text)
    command = any_command if prior.has_handoff else None
    if command is not None and command.kind == "add":
        return Plan("followup", command=command, has_stored=True, extra=incoming.notes)
    text = from_envelope(incoming.text) or incoming.text
    instruction, notes = split_instruction(assemble(text, incoming.files))
    notes = prompts.strip_markers(notes).strip()
    instruction = prompts.strip_markers(instruction) or prior.asked_for
    if word_count(notes) >= MIN_NOTES_WORDS:
        return _build(notes, instruction, incoming.notes)
    if any_command is not None and any_command.kind == "act":
        return Plan("reply", prompts.NO_ACTION, extra=incoming.notes)
    if command is not None:
        return Plan("followup", command=command, has_stored=True, extra=incoming.notes)
    if any_command is not None and incoming.had_history:
        return Plan("reply", prompts.NEED_REPASTE, extra=incoming.notes)
    if (not prior.has_handoff and followup.wants_new_handoff(incoming.text)
            and word_count(incoming.text) <= SHORT_ASK_MAX_WORDS):
        return Plan("reply", prompts.NEED_NOTES, extra=incoming.notes)
    return Plan("scope", has_stored=prior.has_handoff, extra=incoming.notes)


def _build(notes: str, instruction: str, extra: tuple[str, ...]) -> Plan:
    if len(notes) <= MAX_NOTES_CHARS:
        return Plan("build", notes=notes, instruction=instruction, extra=extra)
    cut = prompts.CUT_TO_CAP.format(chars=len(notes), cap=MAX_NOTES_CHARS, left=len(notes) - MAX_NOTES_CHARS)
    return Plan("build", notes=most_recent(notes), instruction=instruction, extra=(*extra, cut))
