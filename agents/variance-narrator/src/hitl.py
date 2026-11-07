"""The two questions this agent may pause on (one per pause, rendered by the chat's request card): no table,
and a missing budget or actual column. Reading an answer fails closed: anything unrecognised cancels."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from inputs import current_message
from nasiko_hitl import MAX_OPTION_LABEL_LEN, question_metadata

METADATA_KEY = "variance_narrator"
NEED_TABLE, MISSING_COLUMN = "need_table", "missing_column"
CANCEL = "Cancel"
MAX_COLUMN_OPTIONS = 9
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    text: str = ""                   # missing_column: the message to re-read once the column is known
    role: str = ""                   # missing_column: "actual" | "budget"
    columns: tuple[str, ...] = ()
    forced: Mapping[str, int] = field(default_factory=dict)
    instruction: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "text": self.text, "role": self.role, "columns": list(self.columns),
                               "forced": dict(self.forced), "instruction": self.instruction,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_TABLE, MISSING_COLUMN):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        forced = {str(k): int(v) for k, v in (data.get("forced") or {}).items()}
        return Pending(kind=data["kind"], text=str(data.get("text") or ""), role=str(data.get("role") or ""),
                       columns=tuple(str(c) for c in data.get("columns") or ()), forced=forced,
                       instruction=str(data.get("instruction") or ""), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                      # run | cancel
    text: str = ""
    forced: Mapping[str, int] = field(default_factory=dict)


def column_options(pending: Pending) -> list[str]:
    taken = set(pending.forced.values())
    labels: list[str] = []
    for index, name in enumerate(pending.columns):
        label = name.strip()[:MAX_OPTION_LABEL_LEN]
        if label and index not in taken and label not in labels and label != CANCEL:
            labels.append(label)
    return labels[:MAX_COLUMN_OPTIONS]


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_TABLE:
        header = "Paste the budget vs actual table"
        text = ("Paste the budget vs actual table (one row per line item, with budget and actual columns, straight "
                "from the spreadsheet) as your answer, or cancel.")
        return text, question_metadata(header, [(CANCEL, "")])
    header = f"Which column is the {pending.role}?"
    text = f"I couldn't find the {pending.role} column in your table. {header}"
    return text, question_metadata(header, [*((c, "") for c in column_options(pending)), (CANCEL, "")],
                                   allow_custom_input=False)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    if pending.kind == NEED_TABLE:
        return Answer("run", "\n".join(p for p in (pending.instruction, answer) if p))
    for index, name in enumerate(pending.columns):
        if name.strip().casefold() == plain and name.strip() in column_options(pending):
            return Answer("run", pending.text, {**pending.forced, pending.role: index})
    return Answer("cancel")
