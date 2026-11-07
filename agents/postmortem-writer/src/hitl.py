"""The one question this agent may pause on: no log to work from. An answer of >= MIN_LOG_WORDS runs the
full flow; a shorter one is re-asked once, then the turn ends; Cancel ends. Reading fails closed."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from inputs import MIN_LOG_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "postmortem_writer"
NEED_SOURCE = "need_source"
CANCEL = "Cancel"
HEADER = "Paste the incident timeline or chat log"
CANCEL_MAX_WORDS = 3

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
    log: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    text = ("Paste the incident timeline or chat log (at least about 80 words) as your answer, and I'll write the "
            "postmortem. Or cancel.")
    if pending.attempts:
        text = "That's still too short for a postmortem. " + text
    return text, question_metadata(HEADER, [(CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if word_count(answer) >= MIN_LOG_WORDS:
        return Answer("run", answer)
    if _CANCEL.match(plain) and word_count(answer) <= CANCEL_MAX_WORDS:
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1))
    return Answer("give_up")
