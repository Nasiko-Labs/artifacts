"""The three questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an answer that isn't recognised cancels, never widens what runs.

Q1 `column`: two columns could both be the status (or title): which one? Q2 `row_cap`: more than 1,500 rows.
Q3 `missing_half`: only the report or only the export was pasted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "status_reality_check"
COLUMN, ROW_CAP, MISSING_HALF = "column", "row_cap", "missing_half"
CANCEL = "Cancel"
USE_RECENT = "Use the 1,500 most recently updated rows"
ROW_CAP_LIMIT = 1_500
MAX_COLUMN_OPTIONS = 9
MIN_PASTE_CHARS = 20

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|use (?:the )?(?:1,?500|most recent))\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    report: str = ""
    export: str = ""
    request: str = ""
    options: dict = field(default_factory=dict)
    columns: dict = field(default_factory=dict)
    field_name: str = ""
    candidates: tuple[str, ...] = ()
    rows: int = 0
    missing: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "report": self.report, "export": self.export,
                               "request": self.request, "options": dict(self.options), "columns": dict(self.columns),
                               "field": self.field_name, "candidates": list(self.candidates), "rows": self.rows,
                               "missing": self.missing, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (COLUMN, ROW_CAP, MISSING_HALF):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], report=str(data.get("report") or ""), export=str(data.get("export") or ""),
                       request=str(data.get("request") or ""), options=dict(data.get("options") or {}),
                       columns={str(k): str(v) for k, v in (data.get("columns") or {}).items()},
                       field_name=str(data.get("field") or ""),
                       candidates=tuple(str(c) for c in data.get("candidates") or ()), rows=int(data.get("rows") or 0),
                       missing=str(data.get("missing") or ""), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel
    report: str = ""
    export: str = ""
    columns: dict = field(default_factory=dict)
    use_recent: bool = False


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == COLUMN:
        header = f"Which column is the ticket {pending.field_name}?"
        text = f"The export has more than one column that could be the ticket {pending.field_name}. {header}"
        options = [(c, "") for c in pending.candidates[:MAX_COLUMN_OPTIONS]] + [(CANCEL, "")]
    elif pending.kind == ROW_CAP:
        header = f"The export has {pending.rows:,} rows, more than {ROW_CAP_LIMIT:,}. What should I do?"
        text = header
        options = [(USE_RECENT, ""), (CANCEL, "")]
    else:
        other = "ticket export" if pending.missing == "export" else "status report"
        header = f"Paste the other half: {other}"
        text = (f"I have the {'status report' if other == 'ticket export' else 'ticket export'}; paste the {other} "
                "as your answer, or cancel.")
        options = [(CANCEL, "")]
    return text, question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or (_CANCEL.match(plain) and len(plain) <= len("never mind")):
        return Answer("cancel")
    if pending.kind == COLUMN:
        chosen = next((c for c in pending.candidates if c.casefold() == plain), None)
        if chosen is None:
            return Answer("cancel")
        return Answer("run", pending.report, pending.export, {**pending.columns, pending.field_name: chosen})
    if pending.kind == ROW_CAP:
        if plain == USE_RECENT.casefold() or _YES.match(plain):
            return Answer("run", pending.report, pending.export, dict(pending.columns), use_recent=True)
        return Answer("cancel")
    if len(answer) < MIN_PASTE_CHARS:
        return Answer("cancel")
    if pending.missing == "export":
        return Answer("run", pending.report, answer, dict(pending.columns))
    return Answer("run", answer, pending.export, dict(pending.columns))
