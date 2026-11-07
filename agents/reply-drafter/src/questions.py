"""Pure: the HITL questions (one per pause, in the order incoming, purpose, channel) and reading the
answer back into a request.

The pause lives on the A2A task (its metadata); the answer arrives on the same task. Exact option label ->
mapped value; any other text -> taken literally; empty or "Cancel" -> no reply drafted."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from inputs import Request
from nasiko_hitl import question_metadata
from prompts import NO_DRAFT

METADATA_KEY = "reply_drafter"
MAX_PAUSES = 3
CANCEL = "Cancel"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."


@dataclass(frozen=True)
class Ask:
    header: str
    lead: str
    options: tuple[tuple[str, str], ...]   # (label, value)


ASKS = {
    "incoming": Ask("Paste the message you're replying to",
                    "Paste the message you want to reply to (the email, chat or text you received).",
                    ((CANCEL, ""),)),
    "purpose": Ask("What do you want to say back?",
                   "What should the reply say? Pick one, or better, type it (\"say yes but not before Thursday\").",
                   (("Say yes", "say yes"), ("Say no or decline", "say no or decline politely"),
                    ("Ask for more time", "ask for more time"), ("Ask a question", "ask a question"),
                    ("Say thanks", "say thanks"))),
    "channel": Ask("Where will you send this?", "Where will you send the reply?",
                   (("Email", "email"), ("Slack or Teams", "chat"), ("Text message", "text"))),
}


@dataclass(frozen=True)
class Pending:
    slot: str                           # incoming | purpose | channel
    text: str                           # the request being answered (no examples)
    org: tuple[str, ...] = ()
    slots: Mapping[str, str] = field(default_factory=dict)
    pauses: int = 1
    wants_json: bool = False

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"slot": self.slot, "text": self.text, "org": list(self.org),
                               "slots": dict(self.slots), "pauses": self.pauses, "wants_json": self.wants_json}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = metadata.get(METADATA_KEY) if metadata else None
        if not isinstance(data, Mapping) or data.get("slot") not in ASKS or not isinstance(data.get("text"), str):
            return None
        # Task metadata round-trips through a protobuf Struct: numbers come back as floats.
        return Pending(slot=data["slot"], text=data["text"], org=tuple(str(o) for o in data.get("org") or ()),
                       slots={str(k): str(v) for k, v in (data.get("slots") or {}).items()},
                       pauses=int(data.get("pauses") or 1), wants_json=bool(data.get("wants_json")))


def question(slot: str) -> tuple[str, dict]:
    """The chat text and the `question_metadata` block for one pause."""
    ask = ASKS[slot]
    return ask.lead, question_metadata(ask.header, [(label, "") for label, _ in ask.options])


def resume(pending: Pending, answer: str) -> Request | str:
    """The request to answer now, or the fixed reply when the user cancelled."""
    text = (answer or "").strip()
    if not text or text.casefold() == CANCEL.casefold():
        return NO_DRAFT
    value = next((v for label, v in ASKS[pending.slot].options if text.casefold() == label.casefold()), text)
    return Request(text=pending.text, org=pending.org, slots={**pending.slots, pending.slot: value},
                   pauses=pending.pauses, wants_json=pending.wants_json)
