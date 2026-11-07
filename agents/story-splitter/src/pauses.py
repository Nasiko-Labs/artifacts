"""The three questions this agent may pause on (one per pause, rendered by the chat's request card) and how an
answer is read. Reading fails closed: Q1 and Q2 fall back to Cancel, Q3 to "Leave as [user type?]".

    Q1 need_epic  the epic is under 25 words      custom text = more epic       [Cancel]
    Q2 too_big    the epic is over 20,000 chars   [Use the first 20,000 characters] [Cancel]
    Q3 who        the epic names no actor          [End users] [Admins] [Developers (API)] [Leave as [user type?]]
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import prompts
from inputs import current_message, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "story_splitter"
NEED_EPIC, TOO_BIG, WHO = "need_epic", "too_big", "who"
MIN_EPIC_WORDS = 25
MAX_EPIC_CHARS = 20_000
MAX_ACTOR_CHARS = 60
CANCEL = "Cancel"
USE_FIRST = "Use the first 20,000 characters"
LEAVE = "Leave as [user type?]"
ACTOR_OPTIONS = {"End users": "end user", "Admins": "admin", "Developers (API)": "developer"}

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_FIRST = re.compile(r"^(?:yes|ok|okay|sure|go|use (?:the )?first|(?:the )?first)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    epic: str = ""
    constraints: tuple[str, ...] = ()
    max_stories: int = 8
    personas: tuple[str, ...] = ()
    sprint_sized: bool = False
    wants_json: bool = False
    chars: int = 0
    outline: dict = field(default_factory=dict)   # Q3 only: the validated outline, so resume skips that call

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "epic": self.epic, "constraints": list(self.constraints),
                               "max_stories": self.max_stories, "personas": list(self.personas),
                               "sprint_sized": self.sprint_sized, "wants_json": self.wants_json,
                               "chars": self.chars, "outline": self.outline}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_EPIC, TOO_BIG, WHO):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], epic=str(data.get("epic") or ""),
                       constraints=tuple(str(c) for c in data.get("constraints") or ()),
                       max_stories=int(data.get("max_stories") or 8),
                       personas=tuple(str(p) for p in data.get("personas") or ()),
                       sprint_sized=bool(data.get("sprint_sized")), wants_json=bool(data.get("wants_json")),
                       chars=int(data.get("chars") or 0), outline=dict(data.get("outline") or {}))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel
    epic: str = ""
    actor: str | None = None    # Q3: the allowed actor, or None for the placeholder
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_EPIC:
        header = "What is the epic about?"
        text = (f"That's too short to split into stories (I need at least {MIN_EPIC_WORDS} words). Describe the "
                "epic: who uses it, what they should be able to do, and any rules or limits. Or cancel.")
        return text, question_metadata(header, [(CANCEL, "")])
    if pending.kind == TOO_BIG:
        header = f"This epic is {pending.chars:,} characters, more than I read at once (20,000). What should I use?"
        text = header + " You can also paste the part you care about."
        return text, question_metadata(header, [(USE_FIRST, ""), (CANCEL, "")])
    header = "Who uses this?"
    text = ("The epic doesn't say who uses this, and I won't invent an actor. Who is it for? Pick one or type your "
            "own; otherwise stories say [user type?].")
    options = [*((label, "") for label in ACTOR_OPTIONS), (LEAVE, "")]
    return text, question_metadata(header, options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if pending.kind == WHO:
        return _who(answer, plain)
    if _CANCEL.match(plain) or not plain:
        return Answer("cancel", reply=prompts.CANCELLED)
    if pending.kind == TOO_BIG:
        if plain == USE_FIRST.casefold() or _FIRST.match(plain):
            return Answer("run", pending.epic)
        if MIN_EPIC_WORDS <= word_count(answer) and len(answer) <= MAX_EPIC_CHARS:
            return Answer("run", answer)
        return Answer("cancel", reply=prompts.CANCELLED)
    combined = f"{pending.epic}\n\n{answer}".strip()
    if word_count(combined) >= MIN_EPIC_WORDS and len(combined) <= MAX_EPIC_CHARS:
        return Answer("run", combined)
    return Answer("cancel", reply="That's still too short to split into stories, so I stopped here. Paste the full "
                                  "epic in a new message when you're ready.")


def _who(answer: str, plain: str) -> Answer:
    for label, actor in ACTOR_OPTIONS.items():
        if plain == label.casefold():
            return Answer("run", actor=actor)
    custom = " ".join(prompts.strip_markers(answer).split()).strip(" .")
    if not custom or plain == LEAVE.casefold() or _CANCEL.match(plain) or len(custom) > MAX_ACTOR_CHARS:
        return Answer("run", actor=None)
    return Answer("run", actor=custom)
