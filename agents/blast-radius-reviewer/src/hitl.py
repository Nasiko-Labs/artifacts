"""The one question this agent pauses on (Q1 "Paste the diff", rendered by the chat's request card) and how an
answer is read. Reading fails closed: anything that is not a diff is a cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from diff_parse import looks_like_diff
from inputs import current_message, split_request
from nasiko_hitl import question_metadata

METADATA_KEY = "blast_radius"
NEED_DIFF = "need_diff"
CANCEL = "Cancel"
HEADER = "Paste the diff"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str = NEED_DIFF
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_DIFF:
            return None
        return Pending(NEED_DIFF, bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                    # run | cancel
    text: str = ""
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    return prompts.NEED_DIFF + " Or cancel.", question_metadata(HEADER, [(CANCEL, "")], allow_custom_input=True)


def interpret(raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if looks_like_diff(split_request(answer).diff):
        return Answer("run", answer)
    if not plain or _CANCEL.match(plain):
        return Answer("cancel", reply=prompts.CANCELLED)
    return Answer("cancel", reply=prompts.NOT_A_DIFF)
