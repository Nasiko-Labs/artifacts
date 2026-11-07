"""Pure: the queue for the current settings, recomputed from the stored snapshot (no refetch), with
`confidence` and `needs_human` decided in code from what was and was not read."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any

import load as load_mod
from facts import PRFacts
from queue_rules import Assessed, Blocked, assess, is_stale, ordered

DEFAULT_STALE_DAYS, MIN_STALE_DAYS, MAX_STALE_DAYS = 3, 1, 30
DEFAULT_WINDOW_DAYS, MIN_WINDOW_DAYS, MAX_WINDOW_DAYS = 14, 1, 60


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass(frozen=True)
class Settings:
    repos: tuple[str, ...] = ()
    stale_days: int = DEFAULT_STALE_DAYS
    include_drafts: bool = False
    window_days: int = DEFAULT_WINDOW_DAYS
    only: str = ""            # one repository of `repos` to show; "" shows all

    def to_dict(self) -> dict:
        return {**asdict(self), "repos": list(self.repos)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Settings:
        d = dict(data or {})
        return Settings(repos=tuple(d.get("repos") or ()),
                        stale_days=clamp(int(d.get("stale_days") or DEFAULT_STALE_DAYS), MIN_STALE_DAYS, MAX_STALE_DAYS),
                        include_drafts=bool(d.get("include_drafts")),
                        window_days=clamp(int(d.get("window_days") or DEFAULT_WINDOW_DAYS), MIN_WINDOW_DAYS,
                                          MAX_WINDOW_DAYS),
                        only=str(d.get("only") or ""))

    def with_changes(self, **changes: Any) -> Settings:
        return replace(self, **changes)


@dataclass(frozen=True)
class Snapshot:
    """What one read of the code host (or one paste) returned."""

    facts: tuple[PRFacts, ...] = ()
    notes: tuple[str, ...] = ()           # coverage lines, one per thing not read
    failed_repos: tuple[str, ...] = ()
    cut: int = 0                          # pull requests over the cap, not inspected
    partial: bool = False                 # some per-PR read was unavailable
    pasted: bool = False
    read_at: str = ""

    def to_dict(self) -> dict:
        return {"facts": [f.to_dict() for f in self.facts], "notes": list(self.notes),
                "failed_repos": list(self.failed_repos), "cut": self.cut, "partial": self.partial,
                "pasted": self.pasted, "read_at": self.read_at}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Snapshot:
        d = dict(data or {})
        return Snapshot(facts=tuple(PRFacts.from_dict(f) for f in d.get("facts") or ()),
                        notes=tuple(d.get("notes") or ()), failed_repos=tuple(d.get("failed_repos") or ()),
                        cut=int(d.get("cut") or 0), partial=bool(d.get("partial")), pasted=bool(d.get("pasted")),
                        read_at=str(d.get("read_at") or ""))


@dataclass(frozen=True)
class View:
    settings: Settings
    shown_repos: tuple[str, ...]
    open_count: int
    stale: tuple[Assessed, ...]
    in_view: tuple[Assessed, ...]
    blocked: tuple[Blocked, ...]
    load: tuple[load_mod.Load, ...]
    threshold: float
    drafts_excluded: int
    notes: tuple[str, ...] = field(default=())
    confidence: str = "high"
    needs_human: bool = False
    pasted: bool = False

    @property
    def oldest_quiet(self) -> int:
        return max((a.quiet_days for a in self.in_view), default=0)


def build(snapshot: Snapshot, settings: Settings, now: datetime) -> View:
    shown = (settings.only,) if settings.only else settings.repos
    facts = [f for f in snapshot.facts if f.repo in shown] if shown else list(snapshot.facts)
    drafts = [f for f in facts if f.pr.draft]
    kept = facts if settings.include_drafts else [f for f in facts if not f.pr.draft]
    assessed = [assess(f, now) for f in kept]
    stale = ordered([a for a in assessed if is_stale(a, settings.stale_days, settings.include_drafts)])
    loads, threshold = load_mod.compute(assessed, now, settings.window_days)
    notes = list(snapshot.notes)
    if drafts and not settings.include_drafts:
        notes.append(f"Draft pull requests excluded: {len(drafts)} (say \"include drafts\" to count them).")
    blocked = tuple(b for a in sorted(assessed, key=lambda a: (a.facts.repo, int(a.facts.pr.number)))
                    for b in a.blocked)
    failed = [r for r in snapshot.failed_repos if r in shown or not shown]
    return View(settings, tuple(shown), len(kept), tuple(stale), tuple(assessed), blocked, tuple(loads), threshold,
                len(drafts) if not settings.include_drafts else 0, tuple(notes),
                _confidence(snapshot, failed, kept), _needs_human(assessed, failed), snapshot.pasted)


def _confidence(snapshot: Snapshot, failed: list[str], kept: list[PRFacts]) -> str:
    if failed or snapshot.pasted:
        return "low"
    unread = any(not f.inspected or not f.checks for f in kept)
    return "medium" if snapshot.cut or snapshot.partial or unread else "high"


def _needs_human(assessed: list[Assessed], failed: list[str]) -> bool:
    return bool(failed) or any(b.kind == "awaiting_first_review" for a in assessed for b in a.blocked)
