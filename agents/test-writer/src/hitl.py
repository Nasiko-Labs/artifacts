"""The one question this agent may pause on ("Paste the code to test"), and how an answer is read. Reading
fails closed: an answer with no code is a cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import lang
import prompts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "test_writer"
NEED_CODE = "need_code"
CANCEL = "Cancel"
HEADER = "Paste the code to test"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str = NEED_CODE
    instruction: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_CODE:
            return None
        return Pending(NEED_CODE, str(data.get("instruction") or ""), bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str          # run | cancel
    text: str = ""
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    return prompts.ASK_CODE, question_metadata(HEADER, [(CANCEL, "")], allow_custom_input=True)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel", reply=prompts.CANCELLED)
    if not lang.extract(answer).files:
        return Answer("cancel", reply=prompts.NOT_CODE)
    combined = f"{pending.instruction}\n\n{answer}" if pending.instruction else answer
    return Answer("run", combined)
