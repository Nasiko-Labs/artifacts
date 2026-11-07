"""The one question this agent may pause on (no source pasted yet) and how the answer is read. Reading fails
closed: an answer that isn't config or a cancel is asked once more, then dropped."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import detect
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "alert_rule_reviewer"
NEED_CONFIG = "need_config"
CANCEL = "Cancel"
HEADER = "Paste the alert rules or monitor definitions"
ASK = ("Paste the alert rules or monitor definitions to review (Prometheus rules, Alertmanager config, Datadog "
       "monitors, CloudWatch alarms) as your answer, or cancel.")
STILL_NOT = "That doesn't look like alert configuration yet. "
GAVE_UP = "That still isn't alert configuration, so I stopped here. Paste the rules in a new message any time."

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str = NEED_CONFIG
    instruction: str = ""
    attempts: int = 0
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "attempts": self.attempts,
                               "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_CONFIG:
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(instruction=str(data.get("instruction") or ""), attempts=int(data.get("attempts") or 0),
                       wants_json=bool(data.get("wants_json")))


@dataclass(frozen=True)
class Answer:
    action: str                 # run | cancel | reask | give_up
    text: str = ""
    pending: Pending | None = None


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    text = (STILL_NOT + ASK) if pending.attempts else ASK
    return text, question_metadata(HEADER, [(CANCEL, "")])


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if detect.looks_like_config(answer):
        joined = f"{pending.instruction}\n{answer}" if pending.instruction else answer
        return Answer("run", joined)
    if not plain or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    if pending.attempts == 0:
        return Answer("reask", pending=Pending(instruction=pending.instruction, attempts=1,
                                               wants_json=pending.wants_json))
    return Answer("give_up")
