"""The fact model. A fact is a plain sentence built in code with exact numbers, plus the display strings a
narrative citing it may copy. Facts are ordered by importance when they are numbered (f1 first)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

KINDS = ("change", "peak", "pattern", "step", "average", "ranking", "share", "gap", "mover", "outlier", "quality",
         "finding")


@dataclass(frozen=True)
class Draft:
    kind: str
    text: str
    display: tuple[str, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    text: str
    display: tuple[str, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "text": self.text, "display": list(self.display),
                "values": dict(self.values)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Fact:
        return Fact(str(data.get("id") or ""), str(data.get("kind") or "finding"), str(data.get("text") or ""),
                    tuple(str(d) for d in data.get("display") or ()), dict(data.get("values") or {}))


@dataclass(frozen=True)
class FactSet:
    shape: str                    # time_series | ranking | comparison | other | findings
    facts: tuple[Fact, ...]
    labels: tuple[str, ...] = ()  # every row label and column name: what a narrative may name
    notes: tuple[str, ...] = ()   # coverage and parsing notes, shown under the reply
    points: int = 0               # data rows used
    metric: str = ""

    def story(self) -> list[Fact]:
        return [f for f in self.facts if f.kind != "quality"]

    def quality(self) -> list[Fact]:
        return [f for f in self.facts if f.kind == "quality"]

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    def to_dict(self) -> dict:
        return {"shape": self.shape, "facts": [f.to_dict() for f in self.facts], "labels": list(self.labels),
                "notes": list(self.notes), "points": self.points, "metric": self.metric}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> FactSet:
        return FactSet(str(data.get("shape") or "other"), tuple(Fact.from_dict(f) for f in data.get("facts") or ()),
                       tuple(str(x) for x in data.get("labels") or ()), tuple(str(x) for x in data.get("notes") or ()),
                       int(data.get("points") or 0), str(data.get("metric") or ""))


def number(story: Sequence[Draft], quality: Sequence[Draft]) -> tuple[Fact, ...]:
    """Ids in importance order: the story facts as given, then the quality facts."""
    return tuple(Fact(f"f{n}", d.kind, d.text, d.display, dict(d.values))
                 for n, d in enumerate([*story, *quality], start=1))
