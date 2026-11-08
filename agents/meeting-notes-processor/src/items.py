"""The meeting's records and their plain-dict form (stored in the checkpointer).

Turns, times and speakers come from the parse; topics, decisions and actions are model output that
survived the code checks. Speakers and times of items are always looked up from the cited turn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

NOT_STATED = "Not stated"
UNKNOWN = "Unknown"
KINDS = ("action", "follow_up")


@dataclass(frozen=True)
class Turn:
    id: str          # T1..Tn
    speaker: str     # as labelled, or "Unknown" for raw notes
    time: str        # as displayed ("14:22", "1:02:03"), "" when the source has none
    text: str
    secs: int = -1   # the time in seconds, -1 when unknown


@dataclass(frozen=True)
class Bullet:
    text: str
    turns: tuple[str, ...]
    snippet: str
    item_id: str = ""


@dataclass(frozen=True)
class Topic:
    title: str
    bullets: tuple[Bullet, ...]
    item_id: str = ""


@dataclass(frozen=True)
class Decision:
    text: str
    turn: str
    quote: str
    item_id: str = ""


@dataclass(frozen=True)
class Action:
    task: str
    kind: str        # action | follow_up
    owner: str       # a name, or NOT_STATED
    due: str         # as written, "" when not stated near the quote
    turn: str
    quote: str
    item_id: str = ""


@dataclass(frozen=True)
class Result:
    topics: tuple[Topic, ...] = ()
    decisions: tuple[Decision, ...] = ()
    actions: tuple[Action, ...] = ()
    kept_before_caps: int = 0
    dropped: int = 0        # items the code could not back with a quote in their turn
    capped: int = 0         # items over the section caps
    failed_parts: int = 0
    total_parts: int = 1
    coverage: float = 1.0   # share of the transcript the map step covered
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not (self.topics or self.decisions or self.actions)

    def to_dict(self) -> dict:
        return {"topics": [asdict(t) for t in self.topics], "decisions": [asdict(d) for d in self.decisions],
                "actions": [asdict(a) for a in self.actions], "kept_before_caps": self.kept_before_caps,
                "dropped": self.dropped, "capped": self.capped, "failed_parts": self.failed_parts,
                "total_parts": self.total_parts, "coverage": self.coverage, "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Result:
        data = data or {}
        topics = tuple(Topic(title=str(t.get("title") or ""), item_id=str(t.get("item_id") or ""),
                             bullets=tuple(_bullet(b) for b in t.get("bullets") or ()))
                       for t in data.get("topics") or ())
        return Result(topics=topics, decisions=tuple(_rebuild(Decision, d) for d in data.get("decisions") or ()),
                      actions=tuple(_rebuild(Action, a) for a in data.get("actions") or ()),
                      kept_before_caps=int(data.get("kept_before_caps") or 0), dropped=int(data.get("dropped") or 0),
                      capped=int(data.get("capped") or 0), failed_parts=int(data.get("failed_parts") or 0),
                      total_parts=int(data.get("total_parts") or 1), coverage=float(data.get("coverage", 1.0)),
                      notes=tuple(str(n) for n in data.get("notes") or ()))


def turns_to_state(turns: Sequence[Turn]) -> list[dict]:
    return [asdict(t) for t in turns]


def turns_from_state(data: Sequence[Mapping[str, Any]] | None) -> tuple[Turn, ...]:
    return tuple(Turn(id=str(d.get("id") or ""), speaker=str(d.get("speaker") or UNKNOWN),
                      time=str(d.get("time") or ""), text=str(d.get("text") or ""), secs=int(d.get("secs", -1)))
                 for d in data or ())


def _bullet(data: Mapping[str, Any]) -> Bullet:
    return Bullet(text=str(data.get("text") or ""), turns=tuple(str(t) for t in data.get("turns") or ()),
                  snippet=str(data.get("snippet") or ""), item_id=str(data.get("item_id") or ""))


def _rebuild(cls, data: Mapping[str, Any]):
    return cls(**{k: str(v or "") for k, v in dict(data).items() if k in cls.__dataclass_fields__})
