"""The two questions this agent may pause on (one per pause, shown as the chat's request card) and how an
answer is read. Reading fails closed: anything that is not the go-ahead option is Cancel.

    Q1 over cap:      "That's 412 changes. ..."  -> Use the first 300 | Cancel
    Q2 all internal:  "Everything here looks internal. Write notes anyway?"
                      -> Yes, include internal changes | Cancel
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "release_notes"
OVER_CAP, ALL_INTERNAL = "over_cap", "all_internal"
MAX_CHANGES = 300
MAX_CHARS = 60_000
CANCEL = "Cancel"
USE_FIRST = f"Use the first {MAX_CHANGES}"
INCLUDE = "Yes, include internal changes"
ALL_INTERNAL_HEADER = "Everything here looks internal. Write notes anyway?"

_GO = {OVER_CAP: re.compile(r"^(?:use )?(?:the )?first\b|^yes\b|^ok(?:ay)?\b", re.IGNORECASE),
       ALL_INTERNAL: re.compile(r"^yes\b|^include\b|^ok(?:ay)?\b|^sure\b|^go ahead\b", re.IGNORECASE)}


@dataclass(frozen=True)
class Pending:
    kind: str
    text: str
    count: int = 0
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "text": self.text, "count": self.count,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (OVER_CAP, ALL_INTERNAL):
            return None
        if not isinstance(data.get("text"), str):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], text=data["text"], count=int(data.get("count") or 0),
                       wants_json=bool(data.get("wants_json")))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == OVER_CAP:
        header = f"That's about {pending.count:,} changes. I write notes for up to {MAX_CHANGES} at once."
        text = (f"{header} Should I use the first {MAX_CHANGES} (and the first {MAX_CHARS:,} characters)? You can "
                "also cancel and paste a shorter list.")
        return text, question_metadata(header, [(USE_FIRST, ""), (CANCEL, "")], allow_custom_input=False)
    text = (f"{ALL_INTERNAL_HEADER} All {pending.count} changes look like internal work (chore, CI, tests, "
            "dependency bumps...), which I normally leave out of customer notes.")
    return text, question_metadata(ALL_INTERNAL_HEADER, [(INCLUDE, ""), (CANCEL, "")], allow_custom_input=False)


def go_ahead(pending: Pending, raw_answer: str) -> bool:
    answer = " ".join(current_message(raw_answer or "").split())
    target = USE_FIRST if pending.kind == OVER_CAP else INCLUDE
    return answer.casefold() == target.casefold() or bool(_GO[pending.kind].match(answer))
