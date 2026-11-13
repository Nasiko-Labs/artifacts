"""Pure: what earlier turns left behind, recovered from the platform's chat history (no checkpointer).

- `notes` / `instruction`: the most recent message with enough notes (`inputs.enough_notes`), cut to the cap.
- `changes`: the follow-ups the user sent after it that changed the handoff ("add a note: ...", "mark #4812
  resolved"), in order, so a rebuilt handoff replays them.
- `reply`: our most recent rendered handoff (`**Shift handoff**`), shown again when the notes were trimmed.
- `asked_for`: when our last reply asked for the notes, the request before it (its instruction).
Any field may be empty: the history is budgeted and may have been trimmed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import followup
import prompts
from entries import split_instruction
from history import ASSISTANT, USER, Turn
from inputs import enough_notes, from_envelope, most_recent

REPLY_MARK = "**Shift handoff**"
INSTRUCTION_MAX_CHARS = 200


@dataclass(frozen=True)
class Prior:
    notes: str = ""
    instruction: str = ""
    changes: tuple[followup.Command, ...] = ()
    reply: str = ""
    asked_for: str = ""

    @property
    def has_handoff(self) -> bool:
        return bool(self.notes or self.reply)


def recall(turns: Sequence[Turn]) -> Prior:
    notes, instruction, changes = _last_notes(turns)
    return Prior(notes=notes, instruction=instruction, changes=changes, reply=_last_reply(turns),
                 asked_for=_asked_for(turns))


def _last_notes(turns: Sequence[Turn]) -> tuple[str, str, tuple[followup.Command, ...]]:
    changes: list[followup.Command] = []
    for turn in reversed(turns):
        if turn.role != USER:
            continue
        command = followup.parse_command(turn.text)
        if command is not None and command.kind in ("add", "mark"):
            changes.insert(0, command)
            continue
        instruction, notes = split_instruction(from_envelope(turn.text) or turn.text)
        notes = prompts.strip_markers(notes).strip()
        if enough_notes(instruction, notes):
            return most_recent(notes), prompts.strip_markers(instruction), tuple(changes)
    return "", "", ()


def _last_reply(turns: Sequence[Turn]) -> str:
    for turn in reversed(turns):
        if turn.role == ASSISTANT and REPLY_MARK in turn.text:
            return turn.text
    return ""


def _asked_for(turns: Sequence[Turn]) -> str:
    if len(turns) < 2 or turns[-1].role != ASSISTANT or not turns[-1].text.startswith(prompts.NEED_NOTES_LEAD):
        return ""
    before = turns[-2]
    return prompts.strip_markers(before.text)[:INSTRUCTION_MAX_CHARS] if before.role == USER else ""
