"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: an empty or cancelling answer cancels; nothing else widens what runs.

    Q1 too_short: the brief is under 40 words.  Options: Cancel; typed text = more detail, appended.
    Q2 who:       no user fact and no audience. Options: Existing customers, Internal teams, New prospects,
                  Cancel; typed text = the audience. A long paste is appended to the brief instead.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import prompts
from inputs import MIN_BRIEF_WORDS, current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "prd_writer"
TOO_SHORT, WHO = "too_short", "who"
CANCEL = "Cancel"
AUDIENCES = ("Existing customers", "Internal teams", "New prospects")
MAX_AUDIENCE_CHARS = 200
CANCEL_MAX_WORDS = 3

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    brief: str = ""
    instruction: str = ""
    facts: list[dict] = field(default_factory=list)
    attempts: int = 0
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "brief": self.brief, "instruction": self.instruction,
                               "facts": list(self.facts), "attempts": self.attempts, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (TOO_SHORT, WHO):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        facts = [dict(f) for f in data.get("facts") or () if isinstance(f, Mapping)]
        return Pending(kind=data["kind"], brief=str(data.get("brief") or ""),
                       instruction=str(data.get("instruction") or ""), facts=facts,
                       attempts=int(data.get("attempts") or 0), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | who | cancel | reask | give_up
    brief: str = ""
    audience: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == TOO_SHORT:
        header = "I need a bit more to write a PRD"
        text = ("That's too short to write a PRD from: almost everything in it would be my invention. Type more "
                "detail as your answer (the problem, who it is for, constraints, any data, a deadline), or cancel.")
        if pending.attempts:
            text = "That's still not enough to write a PRD from. " + text
        return text, question_metadata(header, [(CANCEL, "")])
    header = "Who is this for?"
    text = ("Your brief doesn't say who this is for. Pick one, or type the group (for example 'admins on the "
            "Team plan').")
    return text, question_metadata(header, [*((a, "") for a in AUDIENCES), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = prompts.strip_markers(current_message(raw_answer or "")).strip()
    plain = " ".join(answer.casefold().split())
    if not any(c.isalpha() for c in plain) or (_CANCEL.match(plain) and word_count(plain) <= CANCEL_MAX_WORDS):
        return Answer("cancel")
    if pending.kind == TOO_SHORT:
        combined = f"{pending.brief.strip()}\n\n{answer}".strip()
        if word_count(combined) >= MIN_BRIEF_WORDS:
            return Answer("run", brief=combined)
        if pending.attempts == 0:
            return Answer("reask", pending=replace(pending, brief=combined, attempts=1))
        return Answer("give_up")
    if word_count(answer) > MIN_BRIEF_WORDS:  # a long paste is more brief, not an audience
        return Answer("run", brief=f"{pending.brief.strip()}\n\n{answer}")
    label = next((a for a in AUDIENCES if a.casefold() == plain), answer)
    return Answer("who", audience=label[:MAX_AUDIENCE_CHARS])
