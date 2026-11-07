"""Pure data: a chat message as read this turn, a verified decision, and the log document, each with a
JSON-safe dict form (they live in the checkpointer and on the A2A task between turns)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

KINDS = ("explicit", "implied")
DOC_STATES = ("found", "created", "create", "none")


def text_of(value: Any) -> str:
    return str(value).strip() if value is not None and not isinstance(value, (dict, list)) else ""


def _load(cls, data: Mapping[str, Any] | None):
    """Every field from `data` as text (None for an empty optional, a bool for a bool field)."""
    data = data or {}
    values = {}
    for f in fields(cls):
        raw = data.get(f.name)
        if f.type == "bool":
            values[f.name] = bool(raw)
        elif f.type == "str | None":
            values[f.name] = text_of(raw) or None
        else:
            values[f.name] = text_of(raw)
    return cls(**values)


@dataclass(frozen=True)
class Message:
    """One chat message whose text and timestamp came back from a tool (or the paste) this turn."""

    id: str
    channel: str
    channel_name: str
    author: str
    author_name: str
    ts: str
    day: str          # YYYY-MM-DD, computed in code from `ts`
    text: str
    link: str = ""
    thread: str = ""

    @property
    def who(self) -> str:
        return self.author_name or self.author or "unknown"

    @property
    def where(self) -> str:
        return self.channel_name or self.channel or "unknown"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Message:
        return _load(Message, data)


@dataclass(frozen=True)
class Decision:
    """A decision whose quote was found verbatim in this turn's input; everything but `decision` comes from
    the message, never from the model."""

    id: str
    decision: str
    decided_by: str
    decided_at: str   # YYYY-MM-DD
    channel: str
    quote: str
    kind: str = "explicit"
    link: str | None = None
    proposer: str | None = None
    duplicate_of: str | None = None
    supersedes: str | None = None        # the id of the earlier decision this one reverses
    supersedes_date: str | None = None

    @property
    def implied(self) -> bool:
        return self.kind == "implied"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Decision:
        loaded = _load(Decision, data)
        kind = loaded.kind if loaded.kind in KINDS else "explicit"
        return Decision(**{**asdict(loaded), "kind": kind})


@dataclass(frozen=True)
class Doc:
    """The log document. `state`: found (read this turn), create (the user chose a new doc), none."""

    title: str
    id: str = ""
    link: str = ""
    state: str = "none"
    version: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Doc | None:
        if not isinstance(data, Mapping) or not data.get("title"):
            return None
        loaded = _load(Doc, data)
        return loaded if loaded.state in DOC_STATES else Doc(**{**asdict(loaded), "state": "none"})
