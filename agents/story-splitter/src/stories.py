"""The story model. A story is identified by a stable `key` ("s3", "s3b", "m4"); `depends_on` holds keys, so
renumbering after a split, merge or drop never breaks a dependency. Numbers (`n`) exist only when rendered."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import sizing

PLACEHOLDER_ACTOR = "[user type?]"
PLACEHOLDER_BENEFIT = "[benefit?]"
PATTERNS = ("workflow_step", "operation", "rule_variation", "data_variation", "interface_variation",
            "defer_quality", "spike")
SPIKE = "spike"
SPIKE_CRITERION = ("Given the open question this spike covers, when the time box ends, then the question is "
                   "answered in writing")


@dataclass(frozen=True)
class Story:
    key: str
    title: str
    as_a: str
    i_want: str
    so_that: str
    slice_pattern: str = "workflow_step"
    basis: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    facts: sizing.Facts = field(default_factory=sizing.Facts)
    acceptance: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_spike(self) -> bool:
        return self.slice_pattern == SPIKE

    @property
    def size(self) -> str:
        """From the actual criteria count once acceptance is written, else from the reported estimate."""
        facts = replace(self.facts, criteria=len(self.acceptance)) if self.acceptance else self.facts
        return sizing.size_of(facts, self.is_spike)

    def with_note(self, note: str) -> Story:
        return self if note in self.notes else replace(self, notes=(*self.notes, note))

    def to_state(self) -> dict:
        return {"key": self.key, "title": self.title, "as_a": self.as_a, "i_want": self.i_want,
                "so_that": self.so_that, "slice_pattern": self.slice_pattern, "basis": list(self.basis),
                "depends_on": list(self.depends_on), "facts": self.facts.to_dict(),
                "acceptance": list(self.acceptance), "notes": list(self.notes)}

    @staticmethod
    def from_state(data: dict) -> Story:
        return Story(key=str(data["key"]), title=str(data["title"]), as_a=str(data["as_a"]),
                     i_want=str(data["i_want"]), so_that=str(data["so_that"]),
                     slice_pattern=str(data.get("slice_pattern") or "workflow_step"),
                     basis=tuple(data.get("basis") or ()), depends_on=tuple(data.get("depends_on") or ()),
                     facts=sizing.Facts.from_dict(data.get("facts")),
                     acceptance=tuple(data.get("acceptance") or ()), notes=tuple(data.get("notes") or ()))


def to_output(stories: list[Story], skeleton_key: str = "") -> list[dict]:
    """The envelope's story list: numbered in order, dependencies as numbers."""
    number = {s.key: n for n, s in enumerate(stories, start=1)}
    return [{"n": number[s.key], "title": s.title, "as_a": s.as_a, "i_want": s.i_want, "so_that": s.so_that,
             "acceptance": list(s.acceptance), "size": s.size,
             "depends_on": sorted(number[d] for d in s.depends_on if d in number),
             "slice_pattern": s.slice_pattern, "basis": list(s.basis), "notes": list(s.notes),
             "walking_skeleton": s.key == skeleton_key} for s in stories]


def states(stories: list[Story]) -> list[dict]:
    return [s.to_state() for s in stories]


def from_states(data: Any) -> list[Story]:
    return [Story.from_state(d) for d in data or () if isinstance(d, dict)]
