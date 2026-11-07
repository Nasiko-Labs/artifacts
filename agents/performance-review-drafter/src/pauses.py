"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: an answer that isn't recognised is Cancel, never a guess.

    Q1 need_evidence  fewer than 5 items: paste more (added to what was sent) or Cancel
    Q2 personal       evidence mentions personal characteristics: Continue (those items left out) or Cancel
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "review_drafter"
NEED_EVIDENCE, PERSONAL = "need_evidence", "personal"
CANCEL, CONTINUE = "Cancel", "Continue"
Q1_HEADER = "I need at least 5 concrete examples"
Q2_HEADER = ("Some evidence mentions personal characteristics. I will leave those items out of the review. "
             "Continue?")

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_CONTINUE = re.compile(r"^(?:continue|yes|y|ok|okay|sure|go|go ahead|proceed|do it)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    text: str = ""                    # what the user sent (the evidence so far)
    wants_json: bool = False
    flagged: tuple[str, ...] = ()     # Q2: "e4 (health)" lines

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "text": self.text, "wants_json": self.wants_json,
                               "flagged": list(self.flagged)}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_EVIDENCE, PERSONAL):
            return None
        return Pending(kind=data["kind"], text=str(data.get("text") or ""), wants_json=bool(data.get("wants_json")),
                       flagged=tuple(str(f) for f in data.get("flagged") or ()))


@dataclass(frozen=True)
class Answer:
    action: str                       # run | cancel
    text: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_EVIDENCE:
        text = ("I need at least 5 concrete examples to draft a fair review: what the person did, when, and the "
                "result, one per line (label them Manager:, Peer:, Self:, Customer:, Metric:, PR: or Kudos:). "
                "Paste them as your answer (they're added to what you sent), or cancel.")
        return text, question_metadata(Q1_HEADER, [(CANCEL, "")], allow_custom_input=True)
    listed = "\n".join(f"- {f}" for f in pending.flagged)
    text = (f"{Q2_HEADER}\n{listed}\nThose items are never shown to the model or used in the draft; the report "
            "lists their ids.")
    return text, question_metadata(Q2_HEADER, [(CONTINUE, ""), (CANCEL, "")], allow_custom_input=False)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    if pending.kind == NEED_EVIDENCE:
        return Answer("run", f"{pending.text}\n{answer}".strip())
    if plain == CONTINUE.casefold() or _CONTINUE.match(plain):
        return Answer("run", pending.text)
    return Answer("cancel")
