"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an unrecognised short answer is Cancel."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from inputs import MIN_SOURCE_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "meeting_notes"
NEED_SOURCE, TOO_BIG = "need_source", "too_big"
CANCEL = "Cancel"
FIRST_PART = "Use the first ~20k words"
LAST_PART = "Use the last ~20k words"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_FIRST = re.compile(r"^(?:use the first|the first|first|start|beginning)\b")
_LAST = re.compile(r"^(?:use the last|the last|last|end|the end)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    my_name: str = ""
    head: str = ""            # too_big: the first part, already cut
    tail: str = ""            # too_big: the last part, already cut
    words: int = 0
    attempts: int = 0
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "my_name": self.my_name,
                               "head": self.head, "tail": self.tail, "words": self.words,
                               "attempts": self.attempts, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, TOO_BIG):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       my_name=str(data.get("my_name") or ""), head=str(data.get("head") or ""),
                       tail=str(data.get("tail") or ""), words=int(data.get("words") or 0),
                       attempts=int(data.get("attempts") or 0), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    source: str = ""
    pending: Pending | None = None
    note: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = "Paste the transcript or notes"
        text = f"Paste the meeting transcript or notes (at least about {MIN_SOURCE_WORDS} words) as your answer, or cancel."
        if pending.attempts:
            text = "That's still too short to be a meeting. " + text
        return text, question_metadata(header, [(CANCEL, "")])
    header = f"This is about {pending.words:,} words. What should I do?"
    text = header + " I can process up to about 20k words in one go."
    return text, question_metadata(header, [(FIRST_PART, ""), (LAST_PART, ""), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_SOURCE:
        return _need_source(pending, answer, plain)
    if plain == FIRST_PART.casefold() or _FIRST.match(plain):
        return Answer("run", pending.head, note="That was over my size limit, so I processed the first ~20k words.")
    if plain == LAST_PART.casefold() or _LAST.match(plain):
        return Answer("run", pending.tail, note="That was over my size limit, so I processed the last ~20k words.")
    return Answer("cancel")


def _need_source(pending: Pending, answer: str, plain: str) -> Answer:
    if word_count(answer) >= MIN_SOURCE_WORDS:
        return Answer("run", answer)
    if _CANCEL.match(plain) and word_count(answer) <= 3:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1))
    return Answer("give_up")
