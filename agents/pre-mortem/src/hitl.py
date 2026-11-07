"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: unrecognised short text is Cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message, split_request, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "pre_mortem"
NEED_PLAN, TOO_BIG = "need_plan", "too_big"
MIN_PLAN_WORDS = 80
MAX_PLAN_CHARS = 30_000
CANCEL = "Cancel"
USE_FIRST = "Use the first ~5k words"
USE_LAST = "Use the last ~5k words"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_FIRST = re.compile(r"^(?:use )?(?:the )?first\b")
_LAST = re.compile(r"^(?:use )?(?:the )?last\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    context: tuple[str, ...] = ()
    first: str = ""
    last: str = ""
    words: int = 0
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "context": list(self.context),
                               "first": self.first, "last": self.last, "words": self.words,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_PLAN, TOO_BIG):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       context=tuple(str(c) for c in data.get("context") or ()), first=str(data.get("first") or ""),
                       last=str(data.get("last") or ""), words=int(data.get("words") or 0),
                       wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                    # run | cancel
    plan: str = ""
    context: tuple[str, ...] = ()
    instruction: str = ""
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_PLAN:
        header = "Paste the plan or PRD to pre-mortem"
        return prompts.TOO_SHORT + " Or cancel.", question_metadata(header, [(CANCEL, "")])
    header = f"That plan is about {pending.words:,} words. What should I use?"
    text = (f"{header} I can read up to about 5k words at once (30,000 characters). You can also paste the "
            "section you care about.")
    return text, question_metadata(header, [(USE_FIRST, ""), (USE_LAST, ""), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if word_count(answer) >= MIN_PLAN_WORDS and len(answer) <= MAX_PLAN_CHARS:
        request = split_request(answer)
        return Answer("run", prompts.strip_markers(request.plan), (*pending.context, *request.context),
                      pending.instruction)
    if pending.kind == TOO_BIG and not _CANCEL.match(plain):
        if plain == USE_FIRST.casefold() or _FIRST.match(plain):
            return Answer("run", pending.first, pending.context, pending.instruction)
        if plain == USE_LAST.casefold() or _LAST.match(plain):
            return Answer("run", pending.last, pending.context, pending.instruction)
    if pending.kind == NEED_PLAN and plain and not _CANCEL.match(plain):
        return Answer("cancel", reply="That's still too short for a real pre-mortem, so I stopped here. Paste "
                                      "the full plan in a new message when you're ready.")
    return Answer("cancel", reply=prompts.CANCELLED)
