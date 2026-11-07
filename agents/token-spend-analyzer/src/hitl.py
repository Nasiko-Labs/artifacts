"""The two questions this agent may pause on (one per pause, at most two pauses for one export) and how an answer
is read. Reading fails closed: an answer that isn't recognised never widens what runs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from columns import match_header
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "token_spend_analyzer"
NEED_SOURCE, COLUMN = "need_source", "column"
CANCEL, NONE_OF_THESE = "Cancel", "None of these"
MAX_PAUSES = 2
MAX_OPTIONS = 10
MAX_STORED_CHARS = 600_000
ROLE_WORDS = {"model": "model", "cost": "cost (or token counts)"}
_CANCEL = re.compile(r"^(?:cancel|stop|nope|never ?mind|abort|quit)\b")
_NONE = re.compile(r"^(?:none(?: of these)?|no model(?: column)?|there is none|n/a|no)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    source: str = ""
    source_kind: str = ""
    role: str = ""
    candidates: tuple[str, ...] = ()
    overrides: Mapping[str, str] = field(default_factory=dict)
    pauses: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "source": self.source,
                               "source_kind": self.source_kind, "role": self.role,
                               "candidates": list(self.candidates), "overrides": dict(self.overrides),
                               "pauses": self.pauses}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, COLUMN):
            return None
        overrides = data.get("overrides") if isinstance(data.get("overrides"), Mapping) else {}
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       source=str(data.get("source") or ""), source_kind=str(data.get("source_kind") or ""),
                       role=str(data.get("role") or ""),
                       candidates=tuple(str(c) for c in data.get("candidates") or ()),
                       overrides={str(k): str(v) for k, v in overrides.items()},
                       pauses=int(data.get("pauses") or 1))  # protobuf Struct numbers come back as floats


@dataclass(frozen=True)
class Answer:
    action: str                 # source | column | none | cancel | unknown
    text: str = ""
    header: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = "Paste the usage export (CSV or JSON)"
        text = (header + " as your answer: a provider usage CSV, gateway spend logs, any table with model, token "
                "or cost columns, or Nasiko's FinOps dashboard JSON. Or cancel.")
        return text, question_metadata(header, [(CANCEL, "")])
    header = f"Which column holds the {ROLE_WORDS.get(pending.role, pending.role)}?"
    extra = [(NONE_OF_THESE, "")] if pending.role == "model" else []
    room = MAX_OPTIONS - 1 - len(extra)
    options = [(c, "") for c in _unique(pending.candidates)[:room]] + extra + [(CANCEL, "")]
    text = f"{header} I couldn't tell from the header row. Pick one, or type the column name."
    return text, question_metadata(header, options)


def _unique(names: tuple[str, ...]) -> list[str]:
    seen, out = set(), []
    for n in (x.strip() for x in names):
        if n and n not in seen and n not in (CANCEL, NONE_OF_THESE):
            seen.add(n)
            out.append(n)
    return out


def interpret(pending: Pending, raw_answer: str, headers: list[str]) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    short = len(plain.split()) <= 3
    if (not plain and pending.kind == COLUMN) or (_CANCEL.match(plain) and short):
        return Answer("cancel")
    if pending.kind == NEED_SOURCE:
        return Answer("source", text=answer)
    header = match_header(answer, headers)
    if header is not None:
        return Answer("column", header=header)
    if pending.role == "model" and _NONE.match(plain) and short:
        return Answer("none")
    return Answer("unknown")


def storable(source: str) -> str:
    """The export kept on the paused task (the platform state has a size limit)."""
    return source if len(source) <= MAX_STORED_CHARS else ""
