"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an answer that isn't recognised never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import prompts
import thread as thread_parser
from inputs import current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "thread_untangler"
NEED_THREAD, TOO_BIG = "need_thread", "too_big"
CANCEL = "Cancel"
FIRST_PART = "Use the first ~20k words"
LAST_PART = "Use the last ~20k words"
NARROWER_MIN_WORDS = 3
INSTRUCTION_MAX_CHARS = 300

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_FIRST = re.compile(r"^(?:first|the first|use the first|start|beginning|yes|ok|okay|go|go ahead)\b")
_LAST = re.compile(r"^(?:last|the last|use the last|end|latest|most recent)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    head: str = ""            # too_big: the first part, already cut on a line
    tail: str = ""            # too_big: the last part, already cut on a line
    words: int = 0
    attempts: int = 0
    as_json: bool = False
    notes: tuple[str, ...] = ()

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "head": self.head,
                               "tail": self.tail, "words": self.words, "attempts": self.attempts,
                               "as_json": self.as_json, "notes": list(self.notes)}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_THREAD, TOO_BIG):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       head=str(data.get("head") or ""), tail=str(data.get("tail") or ""),
                       words=int(data.get("words") or 0), attempts=int(data.get("attempts") or 0),
                       as_json=bool(data.get("as_json")), notes=tuple(str(n) for n in data.get("notes") or ()))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    source: str = ""
    instruction: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_THREAD:
        header = "Paste the thread to untangle"
        text = "Paste the Slack thread or email chain (at least 3 messages) as your answer, or cancel."
        if pending.attempts:
            text = "I couldn't find 3 messages in that. " + text
        options = [(CANCEL, "")]
    else:
        header = f"This thread is about {pending.words:,} words. What should I do?"
        text = (f"This thread is about {pending.words:,} words, more than I can read in one go (~20k words). "
                "Pick a part, or type a narrower request to apply to the first part.")
        options = [(FIRST_PART, ""), (LAST_PART, ""), (CANCEL, "")]
    lead = "\n".join(pending.notes)
    return "\n\n".join(p for p in (lead, text) if p), question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_THREAD:
        return _need_thread(pending, answer, plain)
    if _CANCEL.match(plain) or not plain:
        return Answer("cancel")
    if plain == FIRST_PART.casefold() or _FIRST.match(plain):
        return Answer("run", pending.head, pending.instruction)
    if plain == LAST_PART.casefold() or _LAST.match(plain):
        return Answer("run", pending.tail, pending.instruction)
    if word_count(answer) >= NARROWER_MIN_WORDS:  # a narrower request, over the first part
        return Answer("run", pending.head, prompts.strip_markers(answer)[:INSTRUCTION_MAX_CHARS])
    return Answer("cancel")


def _need_thread(pending: Pending, answer: str, plain: str) -> Answer:
    if len(thread_parser.parse(prompts.strip_markers(answer)).content) >= thread_parser.MIN_MESSAGES:
        return Answer("run", answer, pending.instruction)
    if _CANCEL.match(plain) and word_count(answer) <= 3:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1, notes=()))
    return Answer("give_up")
