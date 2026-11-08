"""The one question this agent may pause on (no source yet), rendered by the chat's request card, and how an
answer is read. Reading fails closed: an unrecognised answer never runs anything."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

import prompts
from inputs import MIN_SOURCE_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "runbook_generator"
NEED_SOURCE = "need_source"
HEADER = "Describe the service or paste the incident transcript"
CANCEL = "Cancel"
INSTRUCTION_MAX_CHARS = 300

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str = NEED_SOURCE
    instruction: str = ""
    attempts: int = 0
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "attempts": self.attempts,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_SOURCE:
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(instruction=str(data.get("instruction") or ""), attempts=int(data.get("attempts") or 0),
                       wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    text: str = ""
    pending: Pending | None = None


def need_source(instruction: str, wants_json: bool = False) -> Pending:
    return Pending(instruction=prompts.strip_markers(instruction).strip()[:INSTRUCTION_MAX_CHARS],
                   wants_json=wants_json)


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    text = (f"{prompts.TELL_ME_MORE} Describe the service (at least about {MIN_SOURCE_WORDS} words) or paste "
            "the incident transcript as your answer, or cancel.")
    if pending.attempts:
        text = "That's still too short to write a runbook from. " + text
    return text, question_metadata(HEADER, [(CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if word_count(answer) >= MIN_SOURCE_WORDS:
        return Answer("run", answer)
    if not plain or (_CANCEL.match(plain) and word_count(answer) <= 3):
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1))
    return Answer("give_up")
