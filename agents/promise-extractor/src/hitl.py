"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an answer that isn't recognised never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from inputs import MIN_SOURCE_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "promise_extractor"
NEED_SOURCE, TOO_BIG = "need_source", "too_big"
CANCEL = "Cancel"
FIRST = "Scan the first ~20k words"
LAST = "Scan the last ~20k words"
NEED_SOURCE_HEADER = "Paste your sent messages or notes"
TOO_BIG_HEADER = "That's more than I can scan in one go. What should I do?"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_FIRST = re.compile(r"^(?:first|the first|scan the first|start|beginning|yes|ok|okay|go)\b")
_LAST = re.compile(r"^(?:last|the last|scan the last|end|the end|latest|most recent)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    head: str = ""           # too_big: the first part, already cut
    tail: str = ""           # too_big: the last part, already cut
    attempts: int = 0
    notes: tuple[str, ...] = ()

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "head": self.head,
                               "tail": self.tail, "attempts": self.attempts, "notes": list(self.notes)}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, TOO_BIG):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       head=str(data.get("head") or ""), tail=str(data.get("tail") or ""),
                       attempts=int(data.get("attempts") or 0), notes=tuple(str(n) for n in data.get("notes") or ()))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    source: str = ""
    instruction: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = NEED_SOURCE_HEADER
        text = ("Paste the messages you sent or your notes (at least a couple of sentences) as your answer, "
                "or cancel.")
        if pending.attempts:
            text = "That's still too short to scan. " + text
        options = [(CANCEL, "")]
    else:
        header = TOO_BIG_HEADER
        text = header + " (I read up to about 20k words per request.)"
        options = [(FIRST, ""), (LAST, ""), (CANCEL, "")]
    lead = "\n".join(pending.notes)
    return "\n\n".join(p for p in (lead, text) if p), question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_SOURCE:
        return _need_source(pending, answer, plain)
    if plain == LAST.casefold() or _LAST.match(plain):
        return Answer("run", pending.tail, pending.instruction)
    if plain == FIRST.casefold() or _FIRST.match(plain):
        return Answer("run", pending.head, pending.instruction)
    return Answer("cancel")


def _need_source(pending: Pending, answer: str, plain: str) -> Answer:
    if word_count(answer) >= MIN_SOURCE_WORDS:
        return Answer("run", answer, pending.instruction)
    if _CANCEL.match(plain) and word_count(answer) <= 3:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1, notes=()))
    return Answer("give_up")
