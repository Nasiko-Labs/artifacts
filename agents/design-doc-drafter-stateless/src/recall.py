"""Pure: what earlier turns left behind, recovered from the platform's chat history (no checkpointer).

- `source`: the most recent message with enough requirements to draft from (`enough_to_draft`), plus every
  "add a requirement: ..." the user sent after it, so a rebuilt draft has them all.
- `draft_reqs`: when that message was trimmed, the "## Requirements" section of our last draft (with its
  title), to rebuild from instead.
- `questions`: the open questions of our last draft as (text, number), so a rebuilt draft keeps the numbers
  the user saw and answers by.
- `asked_for`: when our last reply asked what is being designed, the request before it, so the next message
  is appended to it (what the paused agent did with an answer).
Any field may be empty: the history is budgeted and may have been trimmed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

import followup
import prompts
from history import ASSISTANT, USER, Turn
from inputs import MIN_WORDS, enough_to_draft, split_request, word_count

DRAFT_MARKS = ("# Design: ", "# ADR: ")
REQUIREMENTS_HEADING = "## Requirements"
MAX_KEPT_CHARS = 2_000
_QUESTION = re.compile(r"^- \*\*Q(\d{1,3})\*\* (.+?)(?: \(blocks [^)]*\))?$", re.MULTILINE)


@dataclass(frozen=True)
class Prior:
    source: str = ""
    draft_reqs: str = ""
    asked_for: str = ""
    questions: tuple[tuple[str, int], ...] = ()

    @property
    def has_doc(self) -> bool:
        return bool(self.source or self.draft_reqs)


def recall(turns: Sequence[Turn]) -> Prior:
    return Prior(source=_last_source(turns), draft_reqs=_draft_requirements(turns), asked_for=_asked_for(turns),
                 questions=_questions(turns))


def _questions(turns: Sequence[Turn]) -> tuple[tuple[str, int], ...]:
    for turn in reversed(turns):
        if turn.role == ASSISTANT and turn.text.startswith(DRAFT_MARKS):
            return tuple((m.group(2).strip(), int(m.group(1))) for m in _QUESTION.finditer(turn.text))
    return ()


def _last_source(turns: Sequence[Turn]) -> str:
    added: list[str] = []
    for turn in reversed(turns):
        if turn.role != USER:
            continue
        command = followup.parse(turn.text)
        if command.kind == "add":
            added.insert(0, command.text)
        elif enough_to_draft(split_request(turn.text)):
            return "\n".join([turn.text, *added])
    return ""


def _draft_requirements(turns: Sequence[Turn]) -> str:
    for turn in reversed(turns):
        if turn.role == ASSISTANT and turn.text.startswith(DRAFT_MARKS):
            title = turn.text.splitlines()[0].split(": ", 1)[-1].strip()
            section = _section(turn.text, REQUIREMENTS_HEADING)
            return f"Design doc for {title}:\n{section}" if word_count(section) >= MIN_WORDS else ""
    return ""


def _section(text: str, heading: str) -> str:
    start = text.find(heading + "\n")
    if start == -1:
        return ""
    body = text[start + len(heading) + 1:]
    end = body.find("\n## ")
    return (body if end == -1 else body[:end]).strip()


def _asked_for(turns: Sequence[Turn]) -> str:
    if len(turns) < 2 or turns[-1].role != ASSISTANT or not turns[-1].text.startswith(prompts.ASKED_LEADS):
        return ""
    before = turns[-2]
    return before.text.strip()[:MAX_KEPT_CHARS] if before.role == USER else ""
