"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an unrecognised answer never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import prompts
from inputs import current_message
from load import split_message
from nasiko_hitl import question_metadata

METADATA_KEY = "csv_to_insight"
NEED_SOURCE, MISSING_COLUMN = "need_source", "missing_column"
CANCEL = "Cancel"
MAX_COLUMN_OPTIONS = 9
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    question: str = ""
    source: str = ""           # missing_column: the table text being analysed
    delimiter: str = ","
    term: str = ""
    options: tuple[str, ...] = ()
    attempts: int = 0

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "question": self.question, "source": self.source,
                               "delimiter": self.delimiter, "term": self.term, "options": list(self.options),
                               "attempts": self.attempts}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, MISSING_COLUMN):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], question=str(data.get("question") or ""),
                       source=str(data.get("source") or ""), delimiter=str(data.get("delimiter") or ","),
                       term=str(data.get("term") or ""), options=tuple(str(o) for o in data.get("options") or ()),
                       attempts=int(data.get("attempts") or 0))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    source: str = ""
    question: str = ""
    pending: Pending | None = None


def missing_column(question: str, source: str, delimiter: str, term: str, numeric: Sequence[str]) -> Pending:
    return Pending(MISSING_COLUMN, question, source, delimiter, term, tuple(numeric[:MAX_COLUMN_OPTIONS]))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = "Paste the CSV (with a header row)"
        text = "Paste the CSV table (a header row and at least 5 rows) as your answer, or cancel."
        if pending.attempts:
            text = "I couldn't find a table in that. " + text
        return text, question_metadata(header, [(CANCEL, "")])
    header = f"Which column is {pending.term}?"
    text = (f"Your question mentions {pending.term}, but no column is named that. {header} Pick one, or type the "
            "column name.")
    options = [(name, "") for name in pending.options] + [(CANCEL, "")]
    return text, question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_SOURCE:
        _, table, _ = split_message(prompts.strip_markers(answer))
        if table:
            return Answer("run", answer, pending.question)
        if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
            return Answer("cancel")
        if pending.attempts == 0:
            return Answer("reask", pending=replace(pending, attempts=1))
        return Answer("give_up")
    if not plain or _CANCEL.match(plain):
        return Answer("cancel")
    column = next((o for o in pending.options if o.casefold() == plain), None)
    column = column or next((o for o in pending.options if o.casefold() in plain), None)
    focus = f"{pending.question} (use the column {column} for {pending.term})" if column else pending.question
    return Answer("run", pending.source, focus)
