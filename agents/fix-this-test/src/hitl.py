"""The two questions this agent may pause on (one per pause, rendered by the chat's request card) and how
an answer is read. Reading fails closed: an unrecognised short answer is Cancel, never a guess."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import files as f
import prompts
from inputs import current_message
from nasiko_hitl import question_metadata

METADATA_KEY = "fix_this_test"
NEED_FILE, NEED_OUTPUT = "need_file", "need_output"
CONTINUE, CANCEL = "Continue without it", "Cancel"
MAX_PAUSES = 3

_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit)\b")
_CONTINUE = re.compile(r"^(?:continue|go ahead|proceed|skip it|without it)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    failure: str = ""
    files: dict[str, str] = field(default_factory=dict)
    missing: str = ""
    rules: dict = field(default_factory=dict)
    wants_json: bool = False
    pauses: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "failure": self.failure, "files": dict(self.files),
                               "missing": self.missing, "rules": dict(self.rules), "wants_json": self.wants_json,
                               "pauses": self.pauses}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_FILE, NEED_OUTPUT):
            return None
        stored = data.get("files") if isinstance(data.get("files"), Mapping) else {}
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(kind=data["kind"], failure=str(data.get("failure") or ""),
                       files={str(k): str(v) for k, v in stored.items()}, missing=str(data.get("missing") or ""),
                       rules=dict(data.get("rules") or {}), wants_json=bool(data.get("wants_json")),
                       pauses=int(data.get("pauses") or 1))


@dataclass(frozen=True)
class Answer:
    action: str                        # run | static | cancel
    failure: str = ""
    files: dict[str, str] = field(default_factory=dict)
    reply: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_OUTPUT:
        return prompts.NEED_OUTPUT, question_metadata("Paste the failing test output", [(CANCEL, "")])
    name = f"`{pending.missing}`" if pending.missing else "the test file and the source file"
    text = (f"The failure involves {name}, which you didn't paste. Paste it starting with `# file: "
            f"{pending.missing or 'path'}`, or continue without it (I'll suggest a fix without running the tests).")
    options = [(CONTINUE, "Suggest a fix without running the tests"), (CANCEL, "")]
    return text, question_metadata("Paste the missing file", options)


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if plain == CANCEL.casefold() or (len(plain) < 40 and _CANCEL.match(plain)):
        return Answer("cancel", reply=prompts.CANCELLED)
    if pending.kind == NEED_FILE and (plain == CONTINUE.casefold() or (len(plain) < 40 and _CONTINUE.match(plain))):
        return Answer("static", pending.failure, dict(pending.files))
    if len(plain) < 3:
        return Answer("cancel", reply=prompts.CANCELLED)
    pasted = f.split(answer)
    if pending.kind == NEED_OUTPUT:
        merged = f.merge(pending.files, pasted.files).files
        return Answer("run", pasted.failure or pending.failure, merged)
    if pasted.files:
        more = pasted.files
    elif pending.missing:
        more = {pending.missing: answer + ("\n" if not answer.endswith("\n") else "")}
    else:
        return Answer("cancel", reply="I couldn't tell which file that is. Start it with `# file: path` and send "
                                      "everything again in a new message.")
    return Answer("run", pending.failure, f.merge(pending.files, more).files)
