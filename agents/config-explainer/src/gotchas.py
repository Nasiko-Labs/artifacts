"""Pure: one gotcha found by code. Ids, severities and texts are set by the analysers, never by the model;
the model may only add a short `so_what` per id."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

SEVERITIES = ("high", "medium", "low", "info")
MAX_SO_WHAT_WORDS = 30


@dataclass(frozen=True)
class Gotcha:
    id: str
    severity: str
    text: str
    evidence: str = ""

    def to_dict(self, so_what: str = "") -> dict:
        data = {"id": self.id, "severity": self.severity, "text": self.text, "evidence": self.evidence}
        if so_what:
            data["so_what"] = so_what
        return data


def ordered(gotchas: Iterable[Gotcha]) -> list[Gotcha]:
    """Most severe first, then by id; one entry per (id, evidence)."""
    unique: dict[tuple[str, str], Gotcha] = {}
    for gotcha in gotchas:
        unique.setdefault((gotcha.id, gotcha.evidence), gotcha)
    return sorted(unique.values(), key=lambda g: (SEVERITIES.index(g.severity), g.id, g.evidence))


def clean_notes(notes: object, ids: Iterable[str]) -> dict[str, str]:
    """The model's `so_what` notes, kept only for known ids and capped in length."""
    if not isinstance(notes, Mapping):
        return {}
    known = set(ids)
    kept = {}
    for key, value in notes.items():
        if key in known and isinstance(value, str) and value.strip():
            kept[key] = " ".join(value.split()[:MAX_SO_WHAT_WORDS])
    return kept


def has_high(gotchas: Iterable[Gotcha]) -> bool:
    return any(g.severity == "high" for g in gotchas)
