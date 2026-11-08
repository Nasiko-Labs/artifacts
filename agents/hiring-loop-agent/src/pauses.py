"""The one question this agent may pause on: "Which candidate?" when several names that share a word with the
requested one were found in email or documents. Reading the answer fails closed: a cancel word cancels;
a picked label is that candidate; any other text is a corrected name, used as typed."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nasiko_hitl import question_metadata

METADATA_KEY = "hiring_loop"
KIND = "candidate"
HEADER = "Which candidate?"
MAX_NAME_CHARS = 80
_CANCEL = re.compile(r"^(?:cancel|stop|nope|never ?mind|abort|quit|skip|none)\b")
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "


@dataclass(frozen=True)
class Pending:
    instruction: str                          # the request as the user sent it
    options: tuple[tuple[str, str], ...]      # (label, exact candidate name)
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": KIND, "instruction": self.instruction, "wants_json": self.wants_json,
                               "options": [{"label": label, "name": name} for label, name in self.options]}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") != KIND or not isinstance(data.get("options"), list):
            return None
        options = tuple((str(o["label"]), str(o["name"])) for o in data["options"]
                        if isinstance(o, Mapping) and o.get("label") and o.get("name"))
        if not options:
            return None
        return Pending(instruction=str(data.get("instruction") or ""), options=options,
                       wants_json=bool(data.get("wants_json")))


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata)."""
    listed = "\n".join(f"- {label}" for label, _ in pending.options)
    text = (f"I found feedback for more than one candidate with that name:\n{listed}\n\nWhich one? Pick a name, or "
            "type the right name.")
    return text, question_metadata(HEADER, [(label, "") for label, _ in pending.options], allow_custom_input=True)


def interpret(pending: Pending, raw_answer: str) -> str | None:
    """The chosen candidate's name, or None to cancel."""
    idx = (raw_answer or "").rfind(_CURRENT_MESSAGE_MARKER)
    answer = (raw_answer if idx == -1 else raw_answer[idx + len(_CURRENT_MESSAGE_MARKER):]).strip()
    plain = " ".join(answer.casefold().split())
    if not plain or _CANCEL.match(plain):
        return None
    for label, name in pending.options:
        if plain in (" ".join(label.casefold().split()), name.casefold()):
            return name
    return " ".join(answer.split())[:MAX_NAME_CHARS]
