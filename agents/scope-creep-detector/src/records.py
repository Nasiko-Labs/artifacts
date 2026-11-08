"""Checked tracker records, the snapshot a follow-up recomputes from, and the computed report.
Frozen: built once by checks.py / scope.py, never edited. Times are aware UTC datetimes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EventKind = Literal["added_to_sprint", "removed_from_sprint", "estimate_changed"]
Source = Literal["history", "created_after_start", "unknown"]
EVENT_KINDS: tuple[str, ...] = ("added_to_sprint", "removed_from_sprint", "estimate_changed")


def parse_time(raw: object) -> datetime | None:
    """An ISO 8601 string (with or without offset; naive = UTC) or Unix seconds (or milliseconds), as an
    aware UTC datetime."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return _from_epoch(float(raw))
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        return _from_epoch(float(text))
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)  # 3.11+: accepts a trailing "Z"
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _from_epoch(seconds: float) -> datetime | None:
    if seconds > 1e11:  # milliseconds
        seconds /= 1000
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


@dataclass(frozen=True)
class Sprint:
    id: str
    name: str
    starts_at: datetime
    ends_at: datetime
    team: str = ""
    url: str = ""


@dataclass(frozen=True)
class SprintIssue:
    key: str
    url: str
    title: str = ""
    estimate: float | None = None
    assignee: str = ""
    status: str = ""
    created_at: datetime | None = None
    creator: str = ""
    in_sprint_now: bool = True
    parent: str = ""


@dataclass(frozen=True)
class HistoryEvent:
    issue_key: str
    at: datetime
    actor: str
    kind: EventKind
    estimate_from: float | None = None
    estimate_to: float | None = None


@dataclass(frozen=True)
class Snapshot:
    """Everything one pull produced, after checks: enough to recompute with another grace period."""

    sprint: Sprint
    issues: tuple[SprintIssue, ...]
    events: tuple[HistoryEvent, ...] = ()
    history_keys: frozenset[str] = frozenset()  # issues whose history was read
    history_missing: bool = False  # no history tool at all
    history_skipped: bool = False  # the history step was skipped for time
    history_capped: int = 0  # issues left out of history by the cap
    other_matches: tuple[str, ...] = ()  # other sprint names the selector also matched
    partial: bool = False  # a step hit its limit and was salvaged

    def to_json(self) -> dict:
        return {
            "sprint": _dump(self.sprint), "issues": [_dump(i) for i in self.issues],
            "events": [_dump(e) for e in self.events], "history_keys": sorted(self.history_keys),
            "history_missing": self.history_missing, "history_skipped": self.history_skipped,
            "history_capped": self.history_capped, "other_matches": list(self.other_matches),
            "partial": self.partial,
        }

    @staticmethod
    def from_json(data: Mapping[str, Any]) -> Snapshot:
        return Snapshot(
            sprint=Sprint(**_load(data["sprint"], ("starts_at", "ends_at"))),
            issues=tuple(SprintIssue(**_load(i, ("created_at",))) for i in data.get("issues", ())),
            events=tuple(HistoryEvent(**_load(e, ("at",))) for e in data.get("events", ())),
            history_keys=frozenset(data.get("history_keys", ())),
            history_missing=bool(data.get("history_missing")), history_skipped=bool(data.get("history_skipped")),
            history_capped=int(data.get("history_capped") or 0), other_matches=tuple(data.get("other_matches", ())),
            partial=bool(data.get("partial")),
        )


def _dump(record: Any) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in record.__dict__.items()}


def _load(data: Mapping[str, Any], time_fields: tuple[str, ...]) -> dict:
    return {k: (parse_time(v) if k in time_fields else v) for k, v in data.items()}


@dataclass(frozen=True)
class AddedItem:
    issue_key: str
    title: str
    url: str
    estimate: float | None
    added_at: datetime
    added_by: str  # "unknown" when no actor was recorded
    source: Source
    assignee: str = ""
    late: bool = False
    likely_swap: bool = False
    removed_at: datetime | None = None
    from_backlog: bool = False  # created before the cutoff, moved in later
    by_automation: bool = False


@dataclass(frozen=True)
class RemovedItem:
    issue_key: str
    title: str
    url: str
    estimate: float | None
    removed_at: datetime
    removed_by: str
    likely_swap: bool = False


@dataclass(frozen=True)
class ScopeReport:
    sprint: Sprint
    grace_hours: int
    cutoff: datetime
    baseline_count: int
    baseline_points: float
    baseline_unestimated: int
    now_count: int
    now_points: float
    added: tuple[AddedItem, ...]
    removed: tuple[RemovedItem, ...]
    in_grace: tuple[AddedItem, ...]
    net_change_pct: float | None
    severity: str  # none | moderate | high | n/a
    by_person: tuple[tuple[str, int, float], ...]
    by_day: tuple[tuple[str, int], ...]
    no_history: tuple[str, ...]  # keys decided by creation date only
    undetermined: tuple[str, ...]  # keys with neither history nor a creation date
    estimates_current: bool  # True when no estimate_changed event was available
    confidence: str
    needs_human: bool
    notes: tuple[str, ...] = field(default=())
