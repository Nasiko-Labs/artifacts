"""The one question this agent may pause on (no source pasted) and how the answer is read. Reading fails
closed: a cancel-like answer cancels; anything else is read as the paste."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "iac_cost_gate"
NEED_SOURCE = "need_source"
CANCEL = "Cancel"
HEADER = "Paste the Terraform or CloudFormation to check"
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != NEED_SOURCE:
            return None
        return Pending(NEED_SOURCE, str(data.get("instruction") or ""))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for the pause."""
    text = (f"{HEADER} as your answer: .tf files (several separated by `# file: name` lines), the output of "
            "`terraform show -json`, or a CloudFormation template. Add a limit such as \"limit $500\" if you want "
            "a gate. Or cancel.")
    return text, question_metadata(HEADER, [(CANCEL, "")])


def is_cancel(raw_answer: str) -> bool:
    plain = " ".join(current_message(raw_answer or "").casefold().split())
    return not plain or (bool(_CANCEL.match(plain)) and len(plain.split()) <= 3)
