"""The two questions this agent may pause on (one per pause, at most two pauses for one export) and how an
answer is read. Reading fails closed: an answer that isn't recognised never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from columns import match_header
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "billing_export_analyzer"
NEED_SOURCE, COLUMN = "need_source", "column"
CANCEL = "Cancel"
MAX_PAUSES = 2
MAX_OPTIONS = 10
MAX_STORED_CHARS = 600_000
ROLE_WORDS = {"date": "date", "service": "service", "cost": "cost"}
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    csv_text: str = ""
    role: str = ""
    candidates: tuple[str, ...] = ()
    overrides: Mapping[str, str] = field(default_factory=dict)
    pauses: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "csv_text": self.csv_text,
                               "role": self.role, "candidates": list(self.candidates),
                               "overrides": dict(self.overrides), "pauses": self.pauses}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, COLUMN):
            return None
        overrides = data.get("overrides") if isinstance(data.get("overrides"), Mapping) else {}
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       csv_text=str(data.get("csv_text") or ""), role=str(data.get("role") or ""),
                       candidates=tuple(str(c) for c in data.get("candidates") or ()),
                       overrides={str(k): str(v) for k, v in overrides.items()},
                       pauses=int(data.get("pauses") or 1))  # protobuf Struct numbers come back as floats


@dataclass(frozen=True)
class Answer:
    action: str                 # source | column | guess | cancel
    text: str = ""
    header: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = "Paste the billing export (CSV with a header row)"
        text = (header + " as your answer: a date, a service and a cost column, plus tags, account, region or "
                "usage type if you have them. Or cancel.")
        return text, question_metadata(header, [(CANCEL, "")])
    header = f"Which column holds the {ROLE_WORDS.get(pending.role, pending.role)}?"
    options = [(c, "") for c in _unique(pending.candidates)[:MAX_OPTIONS - 1]] + [(CANCEL, "")]
    text = f"{header} I couldn't tell from the header row. Pick one, or type the column name."
    return text, question_metadata(header, options)


def _unique(names: tuple[str, ...]) -> list[str]:
    seen, out = set(), []
    for n in names:
        if n.strip() and n.strip() not in seen and n.strip() != CANCEL:
            seen.add(n.strip())
            out.append(n.strip())
    return out


def interpret(pending: Pending, raw_answer: str, headers: list[str]) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == NEED_SOURCE:
        if _CANCEL.match(plain) and len(plain.split()) <= 3:
            return Answer("cancel")
        return Answer("source", text=answer)
    if not plain or plain == CANCEL.casefold() or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    header = match_header(answer, headers)
    if header is not None:
        return Answer("column", header=header)
    return Answer("guess")


def storable(csv_text: str) -> str:
    """The CSV kept on the paused task (the platform state has a size limit)."""
    return csv_text if len(csv_text) <= MAX_STORED_CHARS else ""
