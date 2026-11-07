"""The checked records of one untangled thread and their plain-dict form (stored in the checkpointer).

Every record carries the message it was quoted from. `mine` is not stored: it is computed from the
owner and the user's name each time the result is rendered."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from typing import Any

NOT_STATED = "Not stated"


@dataclass(frozen=True)
class Decision:
    key: str            # the id this turn's checks gave it (c1.d2); reversals refer to it
    text: str
    message_id: str
    speaker: str
    quote: str
    status: str = "current"   # current | reversed
    reversed_in: str = ""


@dataclass(frozen=True)
class Question:
    text: str
    message_id: str
    asked_by: str
    quote: str


@dataclass(frozen=True)
class Action:
    task: str
    owner: str
    due: str
    message_id: str
    quote: str
    status: str = "open"      # open | superseded


@dataclass(frozen=True)
class Reversal:
    decision_key: str
    decision_text: str
    decision_message: str
    later_message_id: str
    speaker: str
    what_changed: str
    quote: str


@dataclass(frozen=True)
class Untangled:
    tldr: str = ""
    decisions: tuple[Decision, ...] = ()
    questions: tuple[Question, ...] = ()
    actions: tuple[Action, ...] = ()
    reversals: tuple[Reversal, ...] = ()
    dropped: int = 0          # items no quote (or no grounded text) could back
    capped: int = 0           # items over the section caps
    messages: int = 0
    people: int = 0
    chunks: int = 1
    confidence: str = "high"
    needs_human: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not (self.decisions or self.questions or self.actions)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Untangled:
        data = dict(data or {})
        return Untangled(
            tldr=str(data.get("tldr") or ""),
            decisions=_records(Decision, data.get("decisions")),
            questions=_records(Question, data.get("questions")),
            actions=_records(Action, data.get("actions")),
            reversals=_records(Reversal, data.get("reversals")),
            dropped=int(data.get("dropped") or 0), capped=int(data.get("capped") or 0),
            messages=int(data.get("messages") or 0), people=int(data.get("people") or 0),
            chunks=int(data.get("chunks") or 1), confidence=str(data.get("confidence") or "high"),
            needs_human=bool(data.get("needs_human")), notes=tuple(str(n) for n in data.get("notes") or ()),
        )


def _records(kind: type, data: Sequence[Mapping[str, Any]] | None) -> tuple:
    names = {f.name for f in fields(kind)}
    return tuple(kind(**{k: str(v or "") for k, v in dict(d).items() if k in names}) for d in data or ())
