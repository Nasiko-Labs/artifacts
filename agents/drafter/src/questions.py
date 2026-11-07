"""Pure: the HITL questions (one per pause) and reading the answer back into a request.

The pause lives on the A2A task (its metadata); the answer arrives on the same task. Exact option
label → mapped value; any other text → taken literally; empty or "Cancel" → no draft."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from inputs import MAX_CONTEXT_CHARS, Request, request_and_context
from nasiko_hitl import question_metadata
from prompts import NO_DRAFT

METADATA_KEY = "drafter"
MAX_PAUSES = 3
CANCEL = "Cancel"
USE_FIRST = f"Use the first {MAX_CONTEXT_CHARS:,} characters"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Ask:
    header: str
    options: tuple[tuple[str, str], ...]   # (label, value)


ASKS = {
    "recipient": Ask("Who is it to?", (("My manager", "my manager"), ("A teammate", "a teammate"),
                                       ("A customer", "a customer"), ("A group or channel", "a group or channel"))),
    "purpose": Ask("What should this message do?", (("Ask for something", "ask for something"),
                                                    ("Give an update", "give an update"), ("Say thanks", "say thanks"),
                                                    ("Decline or say no", "decline or say no"),
                                                    ("Follow up", "follow up"))),
    "channel": Ask("Where will you send this?", (("Email", "email"), ("Slack or Teams", "chat"),
                                                 ("Text message", "text"), ("LinkedIn", "linkedin"))),
    "context": Ask("That's a lot of context. What should I use?", ((USE_FIRST, "first"), (CANCEL, "cancel"))),
}
_LEADS = {
    "recipient": "Who is this message for? Pick one, or type a name and role (\"Priya, my manager\").",
    "purpose": "What should the message achieve? Pick one, or better, type it (\"ask for a one-week extension\").",
    "channel": "Where will you send it?",
    "context": (f"That's more than {MAX_CONTEXT_CHARS:,} characters of context. Should I use the first "
                f"{MAX_CONTEXT_CHARS:,}, or will you paste just the facts that matter?"),
}


@dataclass(frozen=True)
class Pending:
    slot: str                           # recipient | purpose | channel | context
    text: str                           # the request being drafted (no tone sample)
    org: tuple[str, ...] = ()
    slots: Mapping[str, str] = field(default_factory=dict)
    pauses: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"slot": self.slot, "text": self.text, "org": list(self.org),
                               "slots": dict(self.slots), "pauses": self.pauses}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = metadata.get(METADATA_KEY) if metadata else None
        if not isinstance(data, Mapping) or data.get("slot") not in ASKS or not isinstance(data.get("text"), str):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(slot=data["slot"], text=data["text"], org=tuple(str(o) for o in data.get("org") or ()),
                       slots={str(k): str(v) for k, v in (data.get("slots") or {}).items()},
                       pauses=int(data.get("pauses") or 1))


def question(slot: str) -> tuple[str, dict]:
    """The chat text and the `question_metadata` block for one pause."""
    ask = ASKS[slot]
    return _LEADS[slot], question_metadata(ask.header, [(label, "") for label, _ in ask.options])


def resume(pending: Pending, answer: str) -> Request | str:
    """The request to draft now, or the fixed reply when the user cancelled."""
    text = (answer or "").strip()
    if not text or text.casefold() == CANCEL.casefold():
        return NO_DRAFT
    value = _option_value(pending.slot, text)
    if pending.slot == "context":
        return _resume_context(pending, text, value)
    return Request(text=pending.text, org=pending.org, slots={**pending.slots, pending.slot: value},
                   pauses=pending.pauses, accept_long=True)


def _resume_context(pending: Pending, answer: str, value: str | None) -> Request:
    if value == "first":
        return Request(text=pending.text[:MAX_CONTEXT_CHARS], org=pending.org, slots=pending.slots,
                       pauses=pending.pauses, accept_long=True)
    request, _ = request_and_context(pending.text)
    facts = answer[:MAX_CONTEXT_CHARS]
    return Request(text=f"{request[:MAX_CONTEXT_CHARS]}\n\n{facts}", org=pending.org, slots=pending.slots,
                   pauses=pending.pauses, accept_long=True)


def _option_value(slot: str, answer: str) -> str | None:
    for label, value in ASKS[slot].options:
        if answer.casefold() == label.casefold():
            return value
    return None if slot == "context" else answer
