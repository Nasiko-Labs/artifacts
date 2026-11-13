"""The meeting's records.

Turns, times and speakers come from the parse; topics, decisions and actions are model output that
survived the code checks. Speakers and times of items are always looked up from the cited turn."""

from __future__ import annotations

from dataclasses import dataclass, field

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
