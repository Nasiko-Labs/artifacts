"""Pure data: facts taken from the user's text, runbook steps and the runbook. Stored as dicts in the
checkpointer, so every type round-trips through `to_dict` / `from_dict`."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

PHASES = ("triage", "diagnosis", "mitigation", "verify", "escalate", "after")
FACT_KINDS = ("dependency", "symptom", "signal", "step_taken", "known_fix", "contact", "threshold", "url", "other")
INPUT, SUGGESTED = "input", "suggested"


@dataclass(frozen=True)
class Fact:
    id: str
    kind: str
    text: str
    snippet: str
    command: str | None = None


@dataclass(frozen=True)
class Step:
    phase: str
    title: str
    action: str
    kind: str = "changes_state"
    command: str | None = None
    expect: str = ""
    if_not: str = ""
    rollback: str | None = None
    source: str = SUGGESTED
    fact_ids: tuple[str, ...] = ()
    review: bool = False
    n: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["fact_ids"] = list(self.fact_ids)
        return data

    @staticmethod
    def from_dict(data: dict) -> Step:
        known = {f.name for f in fields(Step)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        clean["fact_ids"] = tuple(clean.get("fact_ids") or ())
        clean["n"] = int(clean.get("n") or 0)
        return Step(**clean)

    def with_(self, **changes: Any) -> Step:
        return replace(self, **changes)


@dataclass(frozen=True)
class Runbook:
    title: str
    purpose: str
    service: str
    mode: str
    steps: tuple[Step, ...] = ()
    facts: tuple[Fact, ...] = ()
    words: int = 0
    dropped: int = 0
    rewritten: int = 0
    repair_failed: bool = False
    notes: tuple[str, ...] = ()
    filled: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"title": self.title, "purpose": self.purpose, "service": self.service, "mode": self.mode,
                "steps": [s.to_dict() for s in self.steps], "facts": [asdict(f) for f in self.facts],
                "words": self.words, "dropped": self.dropped, "rewritten": self.rewritten,
                "repair_failed": self.repair_failed, "notes": list(self.notes), "filled": dict(self.filled)}

    @staticmethod
    def from_dict(data: dict) -> Runbook:
        d = data or {}
        return Runbook(title=str(d.get("title") or ""), purpose=str(d.get("purpose") or ""),
                       service=str(d.get("service") or "unknown"), mode=str(d.get("mode") or "description"),
                       steps=tuple(Step.from_dict(s) for s in d.get("steps") or ()),
                       facts=tuple(Fact(**f) for f in d.get("facts") or ()), words=int(d.get("words") or 0),
                       dropped=int(d.get("dropped") or 0), rewritten=int(d.get("rewritten") or 0),
                       repair_failed=bool(d.get("repair_failed")), notes=tuple(d.get("notes") or ()),
                       filled=dict(d.get("filled") or {}))

    def with_(self, **changes: Any) -> Runbook:
        return replace(self, **changes)

    def fact(self, fact_id: str) -> Fact | None:
        return next((f for f in self.facts if f.id == fact_id), None)
