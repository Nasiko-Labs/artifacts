"""Pure: the one HITL question (material missing) and reading the answer back into a request.

The pause lives on the A2A task (its metadata); the answer arrives on the same task. An option label
sets the goal; any other text is the material (preferred); empty or "Cancel" ends with no draft."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from inputs import Request
from nasiko_hitl import question_metadata

METADATA_KEY = "social_post_drafter"
MAX_PAUSES = 1
CANCEL = "Cancel"
NO_DRAFT = "No posts drafted."
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
HEADER = "What should the post be about?"
LEAD = ("What should the post be about? Paste the announcement, notes or idea (best), or pick a kind of post "
        "and I'll ask for the details in the draft.")
OPTIONS = (("A launch or announcement", "announce"), ("A lesson or tip", "educate"), ("An opinion", "opinion"),
           ("A hiring or event post", "hire"))


@dataclass(frozen=True)
class Pending:
    text: str                      # the request being drafted (no voice sample)
    org: tuple[str, ...] = ()
    pauses: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"text": self.text, "org": list(self.org), "pauses": self.pauses}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = metadata.get(METADATA_KEY) if metadata else None
        if not isinstance(data, Mapping) or not isinstance(data.get("text"), str):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(text=data["text"], org=tuple(str(o) for o in data.get("org") or ()),
                       pauses=int(data.get("pauses") or 1))


def question() -> tuple[str, dict]:
    """The chat text and the `question_metadata` block for the pause."""
    return LEAD, question_metadata(HEADER, [(label, "") for label, _ in OPTIONS])


def resume(pending: Pending, answer: str) -> Request | str:
    """The request to draft now, or the fixed reply when the user cancelled."""
    text = (answer or "").strip()
    if not text or text.casefold() == CANCEL.casefold():
        return NO_DRAFT
    for label, goal in OPTIONS:
        if text.casefold() == label.casefold():
            return Request(text=f"{pending.text}\n\nKind of post: {label.lower()}".strip(), org=pending.org,
                           goal=goal, pauses=pending.pauses)
    return Request(text=f"{pending.text}\n\n{text}".strip(), org=pending.org, pauses=pending.pauses)
