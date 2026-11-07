"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: unrecognised short text is Cancel, never a guess.

    Q1 need_prd     no PRD in the message and none stored      options: Cancel (or paste the PRD)
    Q2 no_sections  no headings and no label lines in the PRD  options: Review as one block / Cancel
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message, split_request, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "prd_critic"
NEED_PRD, NO_SECTIONS = "need_prd", "no_sections"
MIN_PRD_WORDS = 150
MAX_PRD_CHARS = 60_000
CANCEL = "Cancel"
ONE_BLOCK = "Review as one block"
NEED_PRD_HEADER = "Paste the PRD to review"
NO_SECTIONS_HEADER = "I can't find sections in this text. Review it as one block?"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")
_YES = re.compile(r"^(?:yes|yep|yeah|sure|ok|okay|go|go ahead|review|review it|one block|as one block|do it|"
                  r"proceed)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    prd: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "prd": self.prd,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_PRD, NO_SECTIONS):
            return None
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       prd=str(data.get("prd") or ""), wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel
    prd: str = ""
    instruction: str = ""
    one_block: bool = False
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_PRD:
        text = "Paste the PRD you want reviewed (at least 150 words) as your answer, or cancel."
        return text, question_metadata(NEED_PRD_HEADER, [(CANCEL, "")])
    text = (NO_SECTIONS_HEADER + " I couldn't find headings like Problem, Goals, Requirements or Metrics. As one "
            "block, I'll ask the model where each section is and score what it finds; or cancel and paste it with "
            "headings.")
    return text, question_metadata(NO_SECTIONS_HEADER, [(ONE_BLOCK, ""), (CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if word_count(answer) >= MIN_PRD_WORDS and len(answer) <= MAX_PRD_CHARS:
        request = split_request(answer)
        return Answer("run", prompts.strip_markers(request.prd), request.instruction or pending.instruction)
    if _CANCEL.match(plain):
        return Answer("cancel", reply=prompts.CANCELLED)
    if pending.kind == NO_SECTIONS and (plain == ONE_BLOCK.casefold() or _YES.match(plain)):
        return Answer("run", pending.prd, pending.instruction, one_block=True)
    if pending.kind == NEED_PRD and plain:
        return Answer("cancel", reply=prompts.TOO_SHORT)
    return Answer("cancel", reply=prompts.CANCELLED)
