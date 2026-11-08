"""The one question this agent may pause on: JSON that looks like JSON but doesn't parse. The answer is
the corrected JSON (typed into the card) or Cancel. Reading fails closed: an answer without JSON is a
cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from inputs import current_message
from nasiko_hitl import MAX_QUESTION_HEADER_LEN, question_metadata

METADATA_KEY = "formatter"
JSON_ERROR = "json_error"
CANCEL = "Cancel"
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""     # the words around the broken JSON ("as slack", ...), kept for the retry
    error: str = ""

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "error": self.error}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != JSON_ERROR:
            return None
        return Pending(kind=JSON_ERROR, instruction=str(data.get("instruction") or ""),
                       error=str(data.get("error") or ""))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    header = f"That JSON didn't parse ({pending.error}). Paste the corrected JSON"
    if len(header.encode("utf-8")) > MAX_QUESTION_HEADER_LEN:
        header = "That JSON didn't parse. Paste the corrected JSON"
    text = (f"That JSON didn't parse ({pending.error}). Paste the corrected JSON as your answer, or cancel. "
            "I format exactly what you give me, so I don't guess at broken JSON.")
    return text, question_metadata(header, [(CANCEL, "")], allow_custom_input=True)


def is_cancel(raw_answer: str) -> bool:
    plain = " ".join(current_message(raw_answer or "").casefold().split())
    return not plain or (_CANCEL.match(plain) is not None and len(plain.split()) <= 3)
