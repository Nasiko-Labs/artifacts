"""Pure: the one pause (Q1, "What kind of config is this?") and reading the answer back.

The pause lives on the A2A task's metadata; the answer arrives on the same task. An option label forces the
kind; other typed text is either a kind in words or, when nothing was pasted, the config itself."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from classify import cue
from nasiko_hitl import question_metadata

METADATA_KEY = "config_explainer"
MAX_PAUSES = 2
CANCEL = "Cancel"
CANCELLED = "OK, nothing explained."
HEADER = "What kind of config is this?"
OPTIONS = (("Regex", "regex"), ("Cron schedule", "cron"), ("IAM policy", "iam"), ("nginx config", "nginx"))
LEAD_AMBIGUOUS = "I can't tell what kind of config this is. Pick one, or type it."
LEAD_EMPTY = "What would you like explained? Pick the kind, or just paste the config."


@dataclass(frozen=True)
class Pending:
    text: str                           # the message being explained ("" when nothing was pasted)
    org: tuple[str, ...] = ()
    wants_json: bool = False
    pauses: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"text": self.text, "org": list(self.org), "wants_json": self.wants_json,
                               "pauses": self.pauses}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = metadata.get(METADATA_KEY) if metadata else None
        if not isinstance(data, Mapping) or not isinstance(data.get("text"), str):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(text=data["text"], org=tuple(str(o) for o in data.get("org") or ()),
                       wants_json=bool(data.get("wants_json")), pauses=int(data.get("pauses") or 1))


@dataclass(frozen=True)
class Resumed:
    text: str
    kind: str | None


def question(pending: Pending, has_config: bool) -> tuple[str, dict]:
    lead = LEAD_AMBIGUOUS if has_config else LEAD_EMPTY
    return lead, question_metadata(HEADER, [(label, "") for label, _ in OPTIONS])


def resume(pending: Pending, answer: str) -> Resumed | str:
    text = (answer or "").strip()
    if not text or text.casefold() == CANCEL.casefold():
        return CANCELLED
    for label, kind in OPTIONS:
        if text.casefold() == label.casefold():
            return Resumed(pending.text, kind)
    named = cue(text)
    if pending.text.strip() and named and len(text) < 60:
        return Resumed(pending.text, named)
    if pending.text.strip():
        return Resumed(f"{pending.text}\n\n{text}", named)
    return Resumed(text, None)
