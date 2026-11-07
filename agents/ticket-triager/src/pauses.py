"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: anything not recognised is Cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "ticket_triager"
TOO_MANY, COLUMN = "too_many", "column"
MAX_TICKETS = 200
MAX_CHARS = 100_000
CANCEL = "Cancel"
FIRST_200 = f"Triage the first {MAX_TICKETS}"
MAX_COLUMN_OPTIONS = 9

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|triage the first|the first|first)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    text: str = ""                    # the user's paste, re-parsed on resume
    count: int = 0
    columns: tuple[str, ...] = ()
    body_column: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "text": self.text, "count": self.count,
                               "columns": list(self.columns), "body_column": self.body_column,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (TOO_MANY, COLUMN):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], text=str(data.get("text") or ""), count=int(data.get("count") or 0),
                       columns=tuple(str(c) for c in data.get("columns") or ()),
                       body_column=str(data.get("body_column") or ""), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                       # run | cancel
    text: str = ""
    body_column: str = ""
    cap: bool = False                 # triage only the first MAX_TICKETS


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == TOO_MANY:
        header = f"That's {pending.count:,} tickets; I triage up to {MAX_TICKETS} at a time. What should I do?"
        text = f"{header} Paste the rest as a second batch afterwards."
        return text, question_metadata(header, [(FIRST_200, ""), (CANCEL, "")], allow_custom_input=False)
    header = "Which column holds the ticket text?"
    text = f"Your export has more than one text column ({', '.join(pending.columns)}). {header}"
    options = [(c, "") for c in pending.columns[:MAX_COLUMN_OPTIONS]] + [(CANCEL, "")]
    return text, question_metadata(header, options, allow_custom_input=False)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    plain = " ".join(current_message(raw_answer or "").casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel")
    if pending.kind == TOO_MANY:
        if plain == FIRST_200.casefold() or _YES.match(plain):
            return Answer("run", pending.text, pending.body_column, cap=True)
        return Answer("cancel")
    for column in pending.columns:
        if plain == column.casefold():
            return Answer("run", pending.text, column)
    return Answer("cancel")
