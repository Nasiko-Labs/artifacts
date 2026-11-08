"""The model-backed part of a turn (attribute -> specific -> write) and the code-only result built from what it
stored. `Stored` is the checkpointed state: records, attributions, written examples and options, so follow-ups
re-group in code with no model call and "add these" processes only new records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import attribute as attribute_mod
import group as group_mod
import specific as specific_mod
import view as view_mod
import write as write_mod
from attribute import Attribution
from llm import TurnDeps
from records import PR, PRAISE, SHIPPED, Record
from write import FALLBACK, Written


@dataclass(frozen=True)
class Stored:
    records: list[Record] = field(default_factory=list)
    attributions: dict[str, Attribution] = field(default_factory=dict)
    written: dict[str, Written] = field(default_factory=dict)
    specific: dict[str, bool] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    view: view_mod.View = field(default_factory=view_mod.View)
    notes: tuple[str, ...] = ()
    deadline_hit: bool = False

    def to_state(self) -> dict:
        return {"records": [r.to_dict() for r in self.records],
                "attributions": {k: v.to_dict() for k, v in self.attributions.items()},
                "written": {k: v.to_dict() for k, v in self.written.items()}, "specific": dict(self.specific),
                "aliases": dict(self.aliases), "view": self.view.to_dict(), "notes": list(self.notes),
                "deadline_hit": self.deadline_hit}

    @staticmethod
    def from_state(data: Mapping[str, Any] | None) -> Stored | None:
        if not data or not data.get("records"):
            return None
        return Stored([Record.from_dict(r) for r in data["records"]],
                      {k: Attribution.from_dict(v) for k, v in (data.get("attributions") or {}).items()},
                      {k: Written.from_dict(v) for k, v in (data.get("written") or {}).items()},
                      {k: bool(v) for k, v in (data.get("specific") or {}).items()}, dict(data.get("aliases") or {}),
                      view_mod.View.from_dict(data.get("view")), tuple(data.get("notes") or ()),
                      bool(data.get("deadline_hit")))

    def merged_with(self, other: Stored) -> Stored:
        return replace(self, records=[*self.records, *other.records],
                       attributions={**self.attributions, **other.attributions},
                       written={**self.written, **other.written}, specific={**self.specific, **other.specific},
                       aliases={**self.aliases, **other.aliases}, notes=(*self.notes, *other.notes),
                       deadline_hit=self.deadline_hit or other.deadline_hit)


def known_names(records: Sequence[Record], aliases: Mapping[str, str], people: Sequence[str]) -> list[str]:
    names = [*aliases.values(), *people, *(o for r in records for o in r.owners), *(r.speaker for r in records)]
    return list(dict.fromkeys(n for n in names if n))


async def process(deps: TurnDeps, records: Sequence[Record], aliases: dict[str, str], people: Sequence[str],
                  context: Sequence[str], prior: Sequence[Record] = ()) -> Stored:
    """Attribute, decide `specific` in code, write and check examples for `records` (only these)."""
    attributions = await attribute_mod.attribute(deps, records, aliases, known_names(records, aliases, people),
                                                 context)
    phrases = specific_mod.work_phrases(r.title for r in [*prior, *records] if r.kind in (PR, SHIPPED))
    specific = {}
    for r in records:
        if r.kind == PRAISE:
            names = [*attributions[r.id].people, r.speaker, *aliases.values()]
            specific[r.id] = specific_mod.is_specific(attribute_mod.credited_text(r), names, phrases)
    merged = group_mod.merges([*prior, *records])
    usable = [(r, attributions[r.id].people) for r in records
              if attributions[r.id].people and r.id not in merged and specific.get(r.id, True)]
    written = await write_mod.write(deps, usable, context)
    return Stored(list(records), attributions, written, specific, dict(aliases),
                  deadline_hit=deps.budget.exhausted)


def _caveats(stored: Stored, persons: Sequence[Mapping], vague: int) -> list[str]:
    out = list(stored.notes)
    if vague:
        out.append(f"{vague} praise message{'s were' if vague != 1 else ' was'} too general to use.")
    for p in persons:
        if p["kind"] == "first_name":
            n = len(p["examples"]) + p["counts"]["unspecific_praise"]
            first = p["name"].split(" (")[0]
            out.append(f"'{first}' appears first-name-only in {n} record{'s' if n != 1 else ''} and was not merged "
                       "with anyone; add an alias (e.g. `" + first + " = Full Name`) to merge it.")
    fallbacks = len({e["record_id"] for p in persons for e in p["examples"] if e["source"] == FALLBACK})
    if stored.deadline_hit:
        out.append("Time or call budget ran out: some examples are the record's own title or quote.")
    elif fallbacks:
        out.append(f"{fallbacks} example{'s use' if fallbacks != 1 else ' uses'} the record's own title or quote "
                   "because the written sentence didn't match the record.")
    return out


def result(stored: Stored) -> dict:
    grouped = group_mod.group(stored.records, stored.attributions, stored.written, stored.specific, stored.aliases)
    return {**grouped, "records": len(stored.records), "deadline_hit": stored.deadline_hit,
            "caveats": _caveats(stored, grouped["per_person"], len(grouped["vague"]))}

