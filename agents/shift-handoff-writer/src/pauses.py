"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: an answer that isn't recognised is Cancel.

Q1 notes over 100,000 chars: `Use the most recent 100,000 characters` / `Cancel`.
Q2 no notes anywhere: "Paste the day's notes" (custom input) / `Cancel`."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import MIN_NOTES_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "shift_handoff"
TOO_BIG, NEED_NOTES = "too_big", "need_notes"
CANCEL = "Cancel"
USE_RECENT = "Use the most recent 100,000 characters"
NEED_NOTES_HEADER = "Paste the day's notes"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|use the most recent|most recent|recent)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    notes: str = ""          # too_big: the most recent part, already cut
    chars: int = 0
    attempts: int = 0

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "notes": self.notes,
                               "chars": self.chars, "attempts": self.attempts}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (TOO_BIG, NEED_NOTES):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       notes=str(data.get("notes") or ""), chars=int(data.get("chars") or 0),
                       attempts=int(data.get("attempts") or 0))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    notes: str = ""
    instruction: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == TOO_BIG:
        header = (f"These notes are about {pending.chars:,} characters, over my 100,000 limit. Use the most "
                  "recent part?")
        return header, question_metadata(header, [(USE_RECENT, ""), (CANCEL, "")], allow_custom_input=False)
    text = "Paste the day's notes (queue notes, ticket updates, a running log) as your answer, or cancel."
    if pending.attempts:
        text = "That's still too short to write a handoff from. " + text
    return text, question_metadata(NEED_NOTES_HEADER, [(CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == TOO_BIG:
        if plain == USE_RECENT.casefold() or _YES.match(plain):
            return Answer("run", pending.notes, pending.instruction)
        return Answer("cancel")
    if word_count(answer) >= MIN_NOTES_WORDS:
        return Answer("run", prompts.strip_markers(answer), pending.instruction)
    if not plain or (_CANCEL.match(plain) and word_count(answer) <= 3):
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=Pending(NEED_NOTES, pending.instruction, attempts=1))
    return Answer("give_up")
