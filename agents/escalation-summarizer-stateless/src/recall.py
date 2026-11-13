"""Pure: what an earlier turn left behind, recovered from the platform's chat history (no checkpointer).

- `thread`: the most recent escalation thread the user pasted (`inputs.is_thread` once the instruction
  paragraph is split off), with the instruction that came with it (its audience / tone / length).
- `reply`: the most recent reply of ours that is a summary result (has the summary header or an Ask line).
- `asked_for`: when our last reply asked the user to paste a thread, the request that prompted it, so the
  paste that follows keeps that request's audience.
Any of them may be empty: the history is budgeted and may have been trimmed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import prompts
from history import ASSISTANT, USER, Turn
from inputs import (
    MAX_THREAD_CHARS,
    envelope_as_thread,
    is_thread,
    keep_tail,
    split_request,
)

RESULT_MARKS = ("**Escalation summary", "**Ask:**", "**Customer impact:**")
INSTRUCTION_MAX_CHARS = 300


@dataclass(frozen=True)
class Prior:
    thread: str = ""
    instruction: str = ""
    reply: str = ""
    asked_for: str = ""

    @property
    def has_material(self) -> bool:
        return bool(self.thread or self.reply)


def recall(turns: Sequence[Turn]) -> Prior:
    thread, instruction = _last_thread(turns)
    return Prior(thread=thread, instruction=instruction, reply=_last_result(turns), asked_for=_asked_for(turns))


def _last_thread(turns: Sequence[Turn]) -> tuple[str, str]:
    for turn in reversed(turns):
        if turn.role != USER:
            continue
        instruction, pasted = split_request(envelope_as_thread(turn.text))
        thread = prompts.strip_markers(pasted)
        if is_thread(thread):
            return keep_tail(thread, MAX_THREAD_CHARS), prompts.strip_markers(instruction)[:INSTRUCTION_MAX_CHARS]
    return "", ""


def _last_result(turns: Sequence[Turn]) -> str:
    for turn in reversed(turns):
        if turn.role == ASSISTANT and any(mark in turn.text for mark in RESULT_MARKS):
            return turn.text
    return ""


def _asked_for(turns: Sequence[Turn]) -> str:
    """The user request just before our last reply, when that reply asked for a thread."""
    if len(turns) < 2 or turns[-1].role != ASSISTANT or not turns[-1].text.startswith(prompts.NEED_THREAD_LEAD):
        return ""
    before = turns[-2]
    return prompts.strip_markers(before.text)[:INSTRUCTION_MAX_CHARS] if before.role == USER else ""
