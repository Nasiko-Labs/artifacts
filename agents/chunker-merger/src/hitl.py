"""The three questions this agent may pause on (one per pause) and how an answer is read. Reading fails
closed: an answer that isn't recognised is a cancel, never a guess.

- need_text: a split request with no text. The answer is the text.
- need_envelopes: a merge with fewer than two results. The answer is more JSON.
- over_cap: the held text would pass 600,000 characters. Split what I have, or Cancel."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import replies
from accumulate import CAP_CHARS
from envelope import extract_values
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "chunker_merger"
NEED_TEXT, NEED_ENVELOPES, OVER_CAP = "need_text", "need_envelopes", "over_cap"
KINDS = (NEED_TEXT, NEED_ENVELOPES, OVER_CAP)
CANCEL = "Cancel"
SPLIT_HELD = "Split what I have"
MIN_TEXT_CHARS = 40
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_SPLIT_HELD = re.compile(r"(?:split what i have|split it|split|yes|ok|okay|go|go ahead|sure)[.!]?")


@dataclass(frozen=True)
class Pending:
    kind: str
    opts: dict = field(default_factory=dict)      # the split options asked for
    envelopes: tuple = ()                         # need_envelopes: the results already pasted
    note: str = ""

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "opts": dict(self.opts), "envelopes": list(self.envelopes),
                               "note": self.note}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in KINDS:
            return None
        envelopes = tuple(e for e in data.get("envelopes") or () if isinstance(e, Mapping))
        return Pending(kind=data["kind"], opts=dict(data.get("opts") or {}),
                       envelopes=tuple(dict(e) for e in envelopes), note=str(data.get("note") or ""))


@dataclass(frozen=True)
class Answer:
    action: str             # cancel | text | values | split_held
    text: str = ""
    values: tuple = ()
    problems: tuple[str, ...] = ()


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_TEXT:
        header, text, options = "Paste the text to split", replies.NEED_TEXT, [(CANCEL, "")]
    elif pending.kind == NEED_ENVELOPES:
        header, text, options = "Paste the other results to merge with", replies.NEED_ENVELOPES, [(CANCEL, "")]
    else:
        header = f"That's more than I can hold ({CAP_CHARS:,} characters). What should I do?"
        text, options = header, [(SPLIT_HELD, ""), (CANCEL, "")]
    lead = pending.note
    return "\n\n".join(p for p in (lead, text) if p), question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    short = len(plain.split()) <= 3
    if not plain or (_CANCEL.match(plain) and short):
        return Answer("cancel")
    if pending.kind == OVER_CAP:
        return Answer("split_held") if _SPLIT_HELD.fullmatch(plain) else Answer("cancel")
    if pending.kind == NEED_TEXT:
        return Answer("text", text=answer) if len(answer) >= MIN_TEXT_CHARS else Answer("cancel")
    pasted = extract_values(answer)
    if not pasted.values:
        return Answer("cancel")
    return Answer("values", values=pasted.values, problems=pasted.problems)
