"""The two questions this agent may pause on (one per pause, the chat's request card) and how an answer is read.
Reading fails closed: an answer that isn't recognised is Cancel.

    Q1 too_big  over 2,000 replies         options: Analyse the first 2,000, Cancel
    Q2 column   the reply column is unclear options: the candidate column names, Cancel
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata
from parse_replies import Reply

METADATA_KEY = "macro_gap_finder"
TOO_BIG, COLUMN = "too_big", "column"
CANCEL = "Cancel"
FIRST = "Analyse the first 2,000"
MAX_REPLIES = 2_000
MAX_KEPT_CHARS = 600_000        # what a pause may carry on the task (the state store caps a value at 1 MB)
MAX_COLUMN_OPTIONS = 9
_YES = re.compile(r"^(?:yes|y|ok|okay|sure|go|go ahead|do it|proceed|analy[sz]e|first|the first)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    paste: str = ""                                  # Q2: the raw paste, re-parsed with the chosen column
    candidates: tuple[str, ...] = ()                 # Q2
    replies: tuple[dict, ...] = ()                   # Q1: the first 2,000 replies, already cut
    macros: tuple[str, ...] = ()
    total: int = 0
    min_freq: int | None = None
    top: int | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "paste": self.paste, "candidates": list(self.candidates),
                               "replies": list(self.replies), "macros": list(self.macros), "total": self.total,
                               "min_freq": self.min_freq, "top": self.top, "notes": list(self.notes)}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (TOO_BIG, COLUMN):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        def number(key: str) -> int | None:
            return int(data[key]) if data.get(key) is not None else None
        return Pending(kind=data["kind"], paste=str(data.get("paste") or ""),
                       candidates=tuple(str(c) for c in data.get("candidates") or ()),
                       replies=tuple(dict(r) for r in data.get("replies") or ()),
                       macros=tuple(str(m) for m in data.get("macros") or ()), total=int(data.get("total") or 0),
                       min_freq=number("min_freq"), top=number("top"),
                       notes=tuple(str(n) for n in data.get("notes") or ()))


@dataclass(frozen=True)
class Answer:
    action: str                       # run | cancel
    column: str = ""
    replies: tuple[Reply, ...] = ()


def first_replies(replies: Sequence[Reply]) -> tuple[tuple[dict, ...], bool]:
    """The first 2,000 replies, each cut so the pause fits the task store (and whether any was cut)."""
    kept = replies[:MAX_REPLIES]
    per_reply = max(200, MAX_KEPT_CHARS // max(len(kept), 1))
    cut = any(len(r.text) > per_reply for r in kept)
    return tuple({**r.to_dict(), "text": r.text[:per_reply]} for r in kept), cut


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == COLUMN:
        header = "Which column holds the agent's reply?"
        options = [(c, "") for c in pending.candidates[:MAX_COLUMN_OPTIONS]] + [(CANCEL, "")]
        text = f"{header} More than one column could be it: {', '.join(pending.candidates)}."
    else:
        header = (f"This paste has {pending.total:,} replies, more than I can analyse at once (2,000). "
                  "What should I do?")
        options = [(FIRST, ""), (CANCEL, "")]
        text = header
    lead = "\n".join(pending.notes)
    return "\n\n".join(p for p in (lead, text) if p), question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    words = " ".join(answer.casefold().split())
    if not words or words == CANCEL.casefold():
        return Answer("cancel")
    if pending.kind == COLUMN:
        chosen = next((c for c in pending.candidates if c.casefold() == words), None)
        return Answer("run", column=chosen) if chosen else Answer("cancel")
    if words == FIRST.casefold() or _YES.match(words):
        return Answer("run", replies=tuple(Reply.from_dict(r) for r in pending.replies))
    return Answer("cancel")
