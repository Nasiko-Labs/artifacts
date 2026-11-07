"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: Q1 unrecognised short text is Cancel; Q2 unrecognised text is
"Leave those parts out"."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "one_on_one_prep"
NEED_INPUT, SENSITIVE = "need_input", "sensitive"
MIN_WORDS = 40
MAX_CHARS = 60_000
CANCEL = "Cancel"
INCLUDE = "Include"
LEAVE_OUT = "Leave those parts out"
NEED_HEADER = "Paste last meeting's notes and recent updates"
SENSITIVE_HEADER = "These notes mention personal or HR matters. Include them in the prep?"
PARTIAL_MAX_CHARS = 8_000

_CANCEL = re.compile(r"^(?:cancel|stop|no thanks|never ?mind|abort|quit)\b")
_INCLUDE = re.compile(r"^(?:include|keep|yes)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    paste: dict | None = None          # split.Paste.to_dict() for SENSITIVE
    flagged: tuple[str, ...] = ()      # the flagged sentences
    partial: str = ""                  # what was already said, for NEED_INPUT
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "paste": self.paste or {}, "flagged": list(self.flagged),
                               "partial": self.partial, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_INPUT, SENSITIVE):
            return None
        paste = data.get("paste")
        return Pending(kind=data["kind"], paste=dict(paste) if isinstance(paste, Mapping) else None,
                       flagged=tuple(str(s) for s in data.get("flagged") or ()),
                       partial=str(data.get("partial") or ""), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str             # include | leave_out | text | cancel
    text: str = ""
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_INPUT:
        return prompts.TOO_LITTLE + " Or cancel.", question_metadata(NEED_HEADER, [(CANCEL, "")])
    n = len(pending.flagged)
    text = (f"{SENSITIVE_HEADER} I found {n} sentence{'s' if n != 1 else ''} about personal or HR matters. "
            "If you leave them out, the model never sees them.")
    options = [(INCLUDE, ""), (LEAVE_OUT, "Recommended: those sentences are removed before anything is read"),
               (CANCEL, "")]
    return text, question_metadata(SENSITIVE_HEADER, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if plain == CANCEL.casefold() or _CANCEL.match(plain):
        return Answer("cancel", reply=prompts.CANCELLED)
    if pending.kind == SENSITIVE:
        if plain == INCLUDE.casefold() or (_INCLUDE.match(plain) and len(plain.split()) <= 4):
            return Answer("include")
        return Answer("leave_out")  # fail closed
    if not plain:
        return Answer("cancel", reply=prompts.CANCELLED)
    return Answer("text", text=(pending.partial + "\n" + answer).strip())
