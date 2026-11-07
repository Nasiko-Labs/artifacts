"""The one pause (Q1 "Paste the diff", rendered by the chat's request card) and how its answer is read.
The pause keeps the rest of the original request (spec, standards, focus) so the answer is just the diff."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "code_reviewer"
HEADER = "Paste the diff"
CANCEL = "Cancel"
MAX_KEPT_CHARS = 15_000
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    request: str = ""          # what the user asked before the diff (spec, standards, focus)
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": "need_diff", "request": self.request[:MAX_KEPT_CHARS],
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != "need_diff":
            return None
        return Pending(str(data.get("request") or ""), bool(data.get("wants_json")))


def question() -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    return prompts.NO_DIFF_LEAD, question_metadata(HEADER, [(CANCEL, "")])


def answer_text(pending: Pending, raw_answer: str) -> str | None:
    """The combined request to run, or None when the user cancelled."""
    answer = current_message(raw_answer or "").strip()
    if not answer or _CANCEL.match(answer.casefold()):
        return None
    return f"{pending.request}\n\n{answer}".strip()
