"""Pure: the audit's data model (one Dashboard per inventory row), the envelope, and the code-computed
confidence and needs_human. Nothing here calls a model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

STATUSES = ("healthy", "stale", "broken", "unused", "unknown")
HIGH_COVERAGE = 0.9
LOW_COVERAGE = 0.6


@dataclass(frozen=True)
class Complaints:
    count: int = 0
    latest_quote: str | None = None
    link: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Complaints:
        data = data or {}
        return Complaints(int(data.get("count") or 0), data.get("latest_quote"), data.get("link"))


@dataclass(frozen=True)
class Dashboard:
    name: str
    owner: str | None
    status: str
    flags: tuple[str, ...]
    viewers: int | None
    last_refresh: date | None
    age_days: int | None
    action: str
    priority: str | None
    reasons: tuple[str, ...]
    complaints: Complaints = field(default_factory=Complaints)

    def to_dict(self) -> dict:
        return {"name": self.name, "owner": self.owner, "status": self.status, "flags": list(self.flags),
                "viewers": self.viewers, "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
                "age_days": self.age_days, "action": self.action, "priority": self.priority,
                "reasons": list(self.reasons), "complaints": self.complaints.to_dict()}

    def with_complaints(self, found: Complaints, reasons: Sequence[str], flags: Sequence[str],
                        action: str) -> Dashboard:
        return replace(self, complaints=found, reasons=(*self.reasons, *reasons), flags=(*self.flags, *flags),
                       action=action)


@dataclass(frozen=True)
class Audit:
    dashboards: tuple[Dashboard, ...]
    source: str
    header_map: Mapping[str, str]
    coverage: tuple[str, ...]
    confidence: str
    truncated: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {s: sum(1 for d in self.dashboards if d.status == s) for s in STATUSES}

    @property
    def retire(self) -> tuple[Dashboard, ...]:
        found = [d for d in self.dashboards if d.action == "retire"]
        return tuple(sorted(found, key=lambda d: (d.viewers if d.viewers is not None else 0, d.name.casefold())))

    @property
    def fix_first(self) -> tuple[Dashboard, ...]:
        return tuple(d for d in self.dashboards if d.action == "fix")

    @property
    def needs_human(self) -> bool:
        return any(d.action == "retire" or (d.action == "fix" and d.priority == "high") for d in self.dashboards)

    @property
    def summary(self) -> str:
        counts = self.counts
        parts = ", ".join(f"{counts[s]} {s}" for s in STATUSES)
        return f"{len(self.dashboards)} dashboards: {parts}"


def confidence_of(dashboards: Sequence[Dashboard], chat_ok: bool, dropped: int = 0) -> str:
    """high: refresh and viewers known for >= 90% of rows, no unknown row, chat read, nothing dropped;
    low: under 60% of rows usable; medium otherwise."""
    total = len(dashboards)
    if not total:
        return "low"
    usable = sum(1 for d in dashboards if d.status != "unknown") / total
    if usable < LOW_COVERAGE:
        return "low"
    both = sum(1 for d in dashboards if d.last_refresh is not None and d.viewers is not None) / total
    if both >= HIGH_COVERAGE and usable == 1 and chat_ok and not dropped:
        return "high"
    return "medium"


def envelope(audit: Audit) -> dict:
    dashboards = [d.to_dict() for d in audit.dashboards]
    return {"summary": audit.summary, "items": dashboards, "confidence": audit.confidence,
            "needs_human": audit.needs_human, "dashboards": dashboards, "counts": audit.counts,
            "retire": [d.name for d in audit.retire], "header_map": dict(audit.header_map),
            "coverage": list(audit.coverage), "source": audit.source}
