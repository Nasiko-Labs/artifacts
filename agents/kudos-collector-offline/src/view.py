"""Pure: what one reply shows from the stored result - the People/Min options, follow-up filters ("only Priya",
"only PRs", "fewer than 2 examples") and the per-person cap of 12 (newest first, no quality judgement)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import identity

PER_PERSON_CAP = 12
UNATTRIBUTED, VAGUE = "unattributed", "vague"


@dataclass(frozen=True)
class View:
    people: tuple[str, ...] = ()          # People: option, exact full-name / handle match
    names: tuple[str, ...] = ()           # follow-up "only Priya": loose match
    kinds: tuple[str, ...] = ()           # pr | praise | shipped
    min_examples: int = 1
    fewer_than: int | None = None
    show_all: bool = False
    show_quotes: bool = False
    section: str | None = None            # unattributed | vague
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {"people": list(self.people), "min_examples": self.min_examples}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> View:
        data = data or {}
        return View(people=tuple(str(p) for p in data.get("people") or ()),
                    min_examples=int(data.get("min_examples") or 1))

    def but(self, **changes: Any) -> View:
        return replace(self, **changes)


def _wanted(person: Mapping, view: View) -> bool:
    handles = person.get("aliases") or ()
    if view.people and not any(identity.matches(q, person["name"], handles) for q in view.people):
        return False
    return not view.names or any(identity.loosely_matches(q, person["name"], handles) for q in view.names)


def apply(result: Mapping[str, Any], view: View) -> dict:
    shown, hidden_min = [], 0
    for person in result["per_person"]:
        if not _wanted(person, view):
            continue
        examples = [e for e in person["examples"] if not view.kinds or e["kind"] in view.kinds]
        n = len(person["examples"])
        if view.fewer_than is not None and n >= view.fewer_than:
            continue
        if view.fewer_than is None and (n < view.min_examples or (view.kinds and not examples)):
            hidden_min += 1 if n < view.min_examples else 0
            continue
        cap = len(examples) if view.show_all else PER_PERSON_CAP
        shown.append({**person, "examples": examples[:cap], "more": max(0, len(examples) - cap)})
    return {**result, "per_person": shown, "hidden_below_min": hidden_min}


def ambiguous_names(persons: Sequence[Mapping]) -> list[str]:
    return [p["name"] for p in persons if p["kind"] == identity.FIRST]
