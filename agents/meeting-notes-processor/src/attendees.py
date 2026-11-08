"""Pure: who spoke, in order of first appearance, with turn counts and speaking share (integer %, floored,
so the shares never sum past 100). Names mentioned but not speaking are not attendees."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from items import UNKNOWN, Turn

SYSTEM_LABELS = frozenset({"transcript", "recording", "system", "unknown", "speaker", "bot", "notetaker"})
NOT_IDENTIFIED = "Not identified (notes have no speaker labels)"


@dataclass(frozen=True)
class Attendee:
    name: str
    turns: int
    share: int   # % of the attendees' turns

    def to_dict(self) -> dict:
        return {"name": self.name, "turns": self.turns, "share": self.share}


def is_system(name: str) -> bool:
    return name.strip().casefold() in SYSTEM_LABELS


def attendees(turns: Sequence[Turn]) -> tuple[Attendee, ...]:
    counts: dict[str, int] = {}
    for turn in turns:
        if turn.speaker == UNKNOWN or is_system(turn.speaker):
            continue
        counts[turn.speaker] = counts.get(turn.speaker, 0) + 1
    total = sum(counts.values())
    return tuple(Attendee(name, n, n * 100 // total) for name, n in counts.items()) if total else ()


def speaker_names(turns: Sequence[Turn]) -> tuple[str, ...]:
    return tuple(a.name for a in attendees(turns))
