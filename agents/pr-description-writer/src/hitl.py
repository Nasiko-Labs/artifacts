"""The one question this agent may pause on (Q1 "Paste the diff", rendered by the chat's request card) and how
the answer is read. Reading fails closed: an answer with no diff in it never runs a draft."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message, split_input
from nasiko_hitl import question_metadata

METADATA_KEY = "pr_description_writer"
NEED_DIFF = "need_diff"
CANCEL = "Cancel"
HEADER = "Paste the diff"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str = NEED_DIFF
    context: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "context": self.context, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_DIFF:
            return None
        return Pending(NEED_DIFF, str(data.get("context") or ""), bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str  # run | cancel
    text: str = ""
    reply: str = ""


def question() -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    return prompts.NEED_DIFF, question_metadata(HEADER, [(CANCEL, "")], allow_custom_input=True)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    if split_input(answer).parsed.found:
        joined = f"{pending.context}\n\n{answer}" if pending.context else answer
        return Answer("run", joined)
    if not answer or _CANCEL.match(" ".join(answer.casefold().split())):
        return Answer("cancel", reply=prompts.CANCELLED)
    return Answer("cancel", reply=prompts.NOT_A_DIFF)
