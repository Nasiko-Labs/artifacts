"""Pure: the model's JSON answer, parsed into a Reply (or a needs-info / off-topic status)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SLOTS = ("incoming", "purpose", "channel")
MAX_ASKS = 5
MAX_ASK_CHARS = 300


@dataclass(frozen=True)
class Ask:
    ask: str
    quote: str          # verbatim from the incoming message (checked in code)


@dataclass(frozen=True)
class Reply:
    subject: str
    draft: str
    alt_shorter: str | None
    asks: tuple[Ask, ...] = ()

    def to_dict(self) -> dict:
        return {"subject": self.subject, "draft": self.draft, "alt_shorter": self.alt_shorter,
                "asks": [[a.ask, a.quote] for a in self.asks]}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Reply | None:
        if not data or not data.get("draft"):
            return None
        return Reply(subject=str(data.get("subject") or ""), draft=str(data["draft"]),
                     alt_shorter=data.get("alt_shorter") or None,
                     asks=tuple(Ask(str(a), str(q)) for a, q in data.get("asks") or ()))


@dataclass(frozen=True)
class Answer:
    status: str                       # ok | needs_info | off_topic | invalid
    reply: Reply | None = None
    missing: tuple[str, ...] = ()


def parse_answer(data: Mapping[str, Any] | None) -> Answer:
    if not isinstance(data, Mapping):
        return Answer("invalid")
    status = data.get("status")
    if status == "off_topic":
        return Answer("off_topic")
    if status == "needs_info":
        missing = data.get("missing") if isinstance(data.get("missing"), list) else []
        return Answer("needs_info", missing=tuple(s for s in SLOTS if s in missing) or ("incoming",))
    draft = data.get("draft")
    if status != "ok" or not isinstance(draft, str) or not draft.strip():
        return Answer("invalid")
    return Answer("ok", Reply(subject=_text(data.get("subject")), draft=draft.strip(),
                              alt_shorter=_text(data.get("alt_shorter")) or None, asks=_asks(data)))


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip().casefold() not in ("null", "none") else ""


def _asks(data: Mapping[str, Any]) -> tuple[Ask, ...]:
    raw = data.get("asks_unaddressed")
    if not isinstance(raw, list):
        return ()
    pairs = ((_text(a.get("ask")), _text(a.get("quote"))) for a in raw if isinstance(a, Mapping))
    return tuple(Ask(a[:MAX_ASK_CHARS], q[:MAX_ASK_CHARS]) for a, q in pairs if a and q)[:MAX_ASKS]
