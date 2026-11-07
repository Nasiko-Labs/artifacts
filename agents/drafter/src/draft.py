"""Pure: the model's JSON answer, parsed into a Draft (or a needs-info / off-topic status)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from register import CHANNELS

SLOTS = ("recipient", "purpose", "channel")
MAX_FACTS = 20
MAX_FACT_CHARS = 150


@dataclass(frozen=True)
class Draft:
    recipient_name: str
    recipient_role: str
    channel: str
    subject: str
    body: str
    facts_used: tuple[tuple[str, str], ...] = ()   # (fact, verbatim source text)

    def to_dict(self) -> dict:
        return {"recipient_name": self.recipient_name, "recipient_role": self.recipient_role,
                "channel": self.channel, "subject": self.subject, "body": self.body,
                "facts_used": [list(f) for f in self.facts_used]}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Draft | None:
        if not data or not data.get("body"):
            return None
        return Draft(recipient_name=str(data.get("recipient_name") or ""),
                     recipient_role=str(data.get("recipient_role") or ""),
                     channel=str(data.get("channel") or "email"), subject=str(data.get("subject") or ""),
                     body=str(data["body"]), facts_used=tuple((str(a), str(b)) for a, b in data.get("facts_used") or ()))


@dataclass(frozen=True)
class Answer:
    status: str                       # ok | needs_info | off_topic | invalid
    draft: Draft | None = None
    kind: str = "new"                 # new | revision
    missing: tuple[str, ...] = ()


def parse_answer(data: Mapping[str, Any] | None, channel: str | None) -> Answer:
    """`channel` is the one the user named (it wins over the model's)."""
    if not isinstance(data, Mapping):
        return Answer("invalid")
    status = data.get("status")
    if status == "off_topic":
        return Answer("off_topic")
    if status == "needs_info":
        missing = data.get("missing") if isinstance(data.get("missing"), list) else []
        return Answer("needs_info", missing=tuple(s for s in SLOTS if s in missing))
    if status != "ok" or not isinstance(data.get("body"), str) or not data["body"].strip():
        return Answer("invalid")
    picked = channel or (data.get("channel") if data.get("channel") in CHANNELS else None)
    if picked is None:
        return Answer("needs_info", missing=("channel",))
    recipient = data.get("recipient") if isinstance(data.get("recipient"), Mapping) else {}
    draft = Draft(recipient_name=_text(recipient.get("name")), recipient_role=_text(recipient.get("role")),
                  channel=picked, subject=_text(data.get("subject")), body=data["body"].strip(),
                  facts_used=_facts(data.get("facts_used")))
    return Answer("ok", draft=draft, kind="revision" if data.get("kind") == "revision" else "new")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip().casefold() not in ("null", "none") else ""


def _facts(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        return ()
    pairs = ((_text(f.get("fact")), _text(f.get("from"))) for f in value if isinstance(f, Mapping))
    return tuple((a[:MAX_FACT_CHARS], b[:MAX_FACT_CHARS]) for a, b in pairs if a and b)[:MAX_FACTS]
