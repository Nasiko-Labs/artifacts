"""Pure: the result of one humanize pass, its confidence (computed in code, never taken from the model) and its
round trip through the checkpointer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from patterns import Detection

OK, CLEAN, FAILED_CHECK, OFF_TOPIC, ENGLISH_ONLY, MODEL_FAILED = (
    "ok", "clean", "failed_check", "off_topic", "english_only", "model_failed")
UNCHANGED = (FAILED_CHECK, OFF_TOPIC, ENGLISH_ONLY, MODEL_FAILED)
HIGH_DENSITY = 2.5
LOW_DENSITY = 1.0


@dataclass(frozen=True)
class Outcome:
    status: str
    text: str
    before: float
    after: float
    changes: tuple[dict, ...] = ()
    dropped: int = 0
    removed: dict[str, int] = field(default_factory=dict)
    found: dict[str, list[str]] = field(default_factory=dict)
    repaired: bool = False
    audited: bool = False
    reason: str = ""

    @property
    def confidence(self) -> str:
        if self.status == CLEAN:
            return "high"
        if self.status in UNCHANGED or self.after >= HIGH_DENSITY:
            return "low"
        if self.after < LOW_DENSITY and not (self.repaired or self.audited or self.dropped):
            return "high"
        return "medium"

    @property
    def needs_human(self) -> bool:
        return self.confidence == "low"

    def to_dict(self) -> dict:
        return {"status": self.status, "text": self.text, "before": self.before, "after": self.after,
                "changes": [dict(c) for c in self.changes], "dropped": self.dropped, "removed": dict(self.removed),
                "found": {k: list(v) for k, v in self.found.items()}, "repaired": self.repaired,
                "audited": self.audited, "reason": self.reason}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Outcome | None:
        if not isinstance(data, Mapping) or not data.get("status"):
            return None
        return Outcome(status=str(data["status"]), text=str(data.get("text") or ""),
                       before=float(data.get("before") or 0), after=float(data.get("after") or 0),
                       changes=tuple(dict(c) for c in data.get("changes") or ()), dropped=int(data.get("dropped") or 0),
                       removed={str(k): int(v) for k, v in (data.get("removed") or {}).items()},
                       found={str(k): [str(p) for p in v] for k, v in (data.get("found") or {}).items()},
                       repaired=bool(data.get("repaired")), audited=bool(data.get("audited")),
                       reason=str(data.get("reason") or ""))


def removed_counts(before: Detection, after: Detection) -> dict[str, int]:
    had, has = before.counts(), after.counts()
    return {cat: had[cat] - has.get(cat, 0) for cat in had if had[cat] > has.get(cat, 0)}


def found_phrases(detection: Detection) -> dict[str, list[str]]:
    return {cat: detection.phrases(cat) for cat in detection.counts()}


def unchanged(status: str, draft: str, before: Detection, reason: str = "") -> Outcome:
    return Outcome(status, draft, before.density, before.density, found=found_phrases(before), reason=reason)
