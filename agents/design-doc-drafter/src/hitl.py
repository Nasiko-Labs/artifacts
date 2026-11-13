"""The one question this agent may pause on (too little input: "What are you designing?") and how an answer
is read. Asked once: the typed answer is appended to what the user already said and the draft runs.
Reading fails closed: "Cancel" or an empty answer drafts nothing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import prompts
from inputs import current_message, split_request, word_count
from nasiko_hitl import question_metadata

METADATA_KEY = "design_doc"
NEED_REQUIREMENTS = "need_requirements"
HEADER = "What are you designing?"
CANCEL = "Cancel"

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    text: str = ""
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "text": self.text, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_REQUIREMENTS:
            return None
        return Pending(NEED_REQUIREMENTS, str(data.get("text") or ""), bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str          # run | cancel
    text: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause. Requirements pasted after a request line but too thin
    get "too short (N words)" and the minimum, never "what are you designing?"."""
    request = split_request(pending.text)
    pasted = request.instruction and request.requirements.strip()
    text = prompts.too_short(word_count(request.requirements)) if pasted else prompts.ASK_REQUIREMENTS
    return text, question_metadata(HEADER, [(CANCEL, "")], allow_custom_input=True)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return Answer("cancel")
    return Answer("run", "\n".join(t for t in (pending.text, answer) if t.strip()))
