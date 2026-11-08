"""The one question this agent may pause on — paste the table — rendered by the chat's request card, and
how an answer is read. Reading fails closed: an unrecognised answer never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from handoff import find_source
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "insight_narrator"
NEED_SOURCE = "need_source"
CANCEL = "Cancel"
HEADER = "Paste the table or the numbers behind the chart"
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    question: str = ""
    attempts: int = 0

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "question": self.question, "attempts": self.attempts}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_SOURCE:
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(NEED_SOURCE, str(data.get("question") or ""), int(data.get("attempts") or 0))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    text: str = ""              # the answer, holding the table
    question: str = ""          # what the user asked before the pause
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    text = "Paste the table (a label column and at least 3 rows of numbers) as your answer, or cancel."
    if pending.attempts:
        text = "I couldn't find a table in that. " + text
    return text, question_metadata(HEADER, [(CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if find_source(answer) is not None:
        return Answer("run", answer, pending.question)
    if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=replace(pending, attempts=1))
    return Answer("give_up")
