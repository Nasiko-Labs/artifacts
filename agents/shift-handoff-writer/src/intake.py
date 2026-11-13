"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                              -> canned intro (no call)
    >= 40 words of notes ─┬─ over 100,000 chars        -> pause Q1: most recent part or cancel
                          └─ otherwise                 -> build the handoff
    a handoff stored ─┬─ a follow-up command           -> view / json / mark / add note / "I don't act"
                      └─ anything else                 -> one scope-check call (decline or the follow-up help)
    nothing stored ─┬─ "tell / send / post ..."        -> "I don't act" (no call)
                    ├─ "write my handoff" with no notes -> pause Q2: paste the notes
                    └─ anything else                   -> one scope-check call (decline or "short enough")
"""

from __future__ import annotations

from dataclasses import dataclass

import followup
import pauses
import prompts
import smalltalk
from entries import split_instruction
from inputs import (
    MAX_NOTES_CHARS,
    Incoming,
    assemble,
    enough_notes,
    from_envelope,
    most_recent,
    word_count,
)

SHORT_ASK_MAX_WORDS = 12


@dataclass(frozen=True)
class Plan:
    route: str                                  # reply | pause | build | followup | scope
    reply: str = ""
    pending: pauses.Pending | None = None
    notes: str = ""
    instruction: str = ""
    command: followup.Command | None = None
    has_stored: bool = False
    extra: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_stored: bool) -> Plan:
    canned = None if incoming.files else smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned, extra=incoming.notes)
    command = followup.parse_command(incoming.text) if has_stored else None
    if command is not None and command.kind == "add":
        return Plan("followup", command=command, has_stored=True, extra=incoming.notes)
    text = from_envelope(incoming.text) or incoming.text
    instruction, notes = split_instruction(assemble(text, incoming.files))
    notes = prompts.strip_markers(notes).strip()
    instruction = prompts.strip_markers(instruction)
    if enough_notes(instruction, notes):
        if len(notes) > MAX_NOTES_CHARS:
            pending = pauses.Pending(pauses.TOO_BIG, instruction, most_recent(notes), chars=len(notes))
            return Plan("pause", pending=pending)
        return Plan("build", notes=notes, instruction=instruction, extra=incoming.notes)
    if command is not None:
        return Plan("followup", command=command, has_stored=True, extra=incoming.notes)
    if followup.parse_command(incoming.text) == followup.Command("act"):
        return Plan("reply", prompts.NO_ACTION, extra=incoming.notes)
    if (not has_stored and followup.wants_new_handoff(incoming.text)
            and word_count(incoming.text) <= SHORT_ASK_MAX_WORDS):
        return Plan("pause", pending=pauses.Pending(pauses.NEED_NOTES, prompts.strip_markers(incoming.text)[:200]))
    return Plan("scope", has_stored=has_stored, extra=incoming.notes)


def plan_resume(answer: pauses.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", "Still no notes, so I stopped. " + smalltalk.INTRO)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    instruction, notes = split_instruction(answer.notes)
    notes = prompts.strip_markers(notes).strip()
    if len(notes) > MAX_NOTES_CHARS:
        notes = most_recent(notes)
    return Plan("build", notes=notes, instruction=answer.instruction or instruction)
