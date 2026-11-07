"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: an unrecognised answer never widens what runs.

- need_source: "Paste the before/after table by segment" (options: Cancel; custom text = the table).
- which_before: "Which column is 'before'?" (options: the number columns + Cancel)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import prompts
from grid import find_grid
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "metric_mover"
NEED_SOURCE, WHICH_BEFORE = "need_source", "which_before"
CANCEL = "Cancel"
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    question: str = ""            # need_source: the user's request so far
    source: str = ""              # which_before: the whole message holding the table
    options: tuple[str, ...] = ()
    attempts: int = 0

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "question": self.question, "source": self.source,
                               "options": list(self.options), "attempts": self.attempts}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, WHICH_BEFORE):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], question=str(data.get("question") or ""),
                       source=str(data.get("source") or ""), options=tuple(str(o) for o in data.get("options") or ()),
                       attempts=int(data.get("attempts") or 0))


@dataclass(frozen=True)
class Answer:
    action: str                     # run | cancel | reask | give_up
    text: str = ""                  # the message to analyse
    forced: tuple[str, str] | None = None
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = "Paste the before/after table by segment"
        text = ("Paste the before/after table by segment (a segment column, then before and after values; add counts "
                "such as sessions and signups if it's a rate) as your answer, or cancel.")
        if pending.attempts:
            text = "I couldn't find a table in that. " + text
        return text, question_metadata(header, [(CANCEL, "")])
    header = "Which column is 'before'?"
    text = (f"I can't tell which number column is before and which is after. {header} Pick one; the next number "
            "column to its right is read as 'after'.")
    return text, question_metadata(header, [(name, "") for name in pending.options] + [(CANCEL, "")])


def after_of(before: str, options: tuple[str, ...]) -> str:
    """The column read as 'after': the next option to the right of `before`, else the one to its left."""
    i = options.index(before)
    return options[i + 1] if i + 1 < len(options) else options[i - 1]


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_SOURCE:
        if find_grid(prompts.strip_markers(answer)) is not None:
            return Answer("run", "\n".join(p for p in (pending.question, answer) if p))
        if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
            return Answer("cancel")
        if pending.attempts == 0:
            return Answer("reask", pending=replace(pending, attempts=1))
        return Answer("give_up")
    if not plain or _CANCEL.match(plain) or len(pending.options) < 2:
        return Answer("cancel")
    column = next((o for o in pending.options if o.casefold() == plain), None)
    column = column or next((o for o in pending.options if o.casefold() in plain), None)
    if column is None:
        return Answer("cancel")
    return Answer("run", pending.source, (column, after_of(column, pending.options)))
