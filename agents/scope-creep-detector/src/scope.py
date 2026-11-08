"""Pure: the scope-change method. Every date, set, count, sum and percentage is decided here, never by
the model.

cutoff = sprint start + grace. Baseline B = issues in the sprint at the cutoff. Added A = issues that
joined after the cutoff (a history `added_to_sprint` event, or, without one, created after the cutoff).
Removed R = baseline issues whose last membership event is a removal after the cutoff. An issue added,
removed and added again counts once, at its last add.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from records import (
    AddedItem,
    HistoryEvent,
    RemovedItem,
    ScopeReport,
    Snapshot,
    SprintIssue,
)

GRACE_HOURS = 24
MAX_GRACE_HOURS = 120
SWAP_WINDOW = timedelta(hours=24)
SWAP_POINTS = 1.0
LATE_FRACTION = 0.75
SCOPE_MODERATE_PCT = 10.0
SCOPE_HIGH_PCT = 25.0
UNKNOWN_ACTOR_SHARE = 0.25
UNKNOWN = "unknown"
_MEMBERSHIP = ("added_to_sprint", "removed_from_sprint")
_BOT = re.compile(r"\[bot\]|\bbot\b|\bautomation\b|\bintegration\b|\bworkflow\b", re.IGNORECASE)


def clamp_grace(hours: float | None) -> int:
    if hours is None:
        return GRACE_HOURS
    return int(min(max(hours, 0), MAX_GRACE_HOURS))


def is_bot(actor: str) -> bool:
    return bool(_BOT.search(actor or ""))


@dataclass(frozen=True)
class Fate:
    kind: str  # baseline | added | removed | gone | undetermined
    item: AddedItem | None = None
    removed: RemovedItem | None = None
    in_grace: AddedItem | None = None


def compute(snap: Snapshot, grace_hours: int, now: datetime) -> ScopeReport:
    sprint = snap.sprint
    grace = clamp_grace(grace_hours)
    cutoff = sprint.starts_at + timedelta(hours=grace)
    late_from = sprint.starts_at + (sprint.ends_at - sprint.starts_at) * LATE_FRACTION
    by_key = _events_by_key(snap.events)
    fates = {i.key: classify(i, by_key.get(i.key, ()), sprint.starts_at, cutoff, late_from) for i in snap.issues}
    issues = {i.key: i for i in snap.issues}
    baseline = [k for k, f in fates.items() if f.kind in ("baseline", "removed")]
    added, removed = _mark_swaps([f.item for f in fates.values() if f.item],
                                 [f.removed for f in fates.values() if f.removed])
    base_est = [estimate_at(issues[k], by_key.get(k, ()), cutoff) for k in baseline]
    added_points = sum(a.estimate or 0 for a in added if a.removed_at is None)
    removed_points = sum(r.estimate or 0 for r in removed)
    baseline_points = sum(e for e in base_est if e is not None)
    pct = round((added_points - removed_points) / baseline_points * 100, 1) if baseline_points > 0 else None
    still = [issues[k] for k, f in fates.items() if f.kind == "baseline"] + \
            [issues[a.issue_key] for a in added if a.removed_at is None]
    no_history = tuple(k for k, f in fates.items() if f.kind != "undetermined" and k not in snap.history_keys
                       and not by_key.get(k))
    undetermined = tuple(k for k, f in fates.items() if f.kind == "undetermined")
    confidence = _confidence(snap, no_history, undetermined)
    unknown = sum(1 for a in added if a.added_by == UNKNOWN)
    needs_human = confidence == "low" or bool(snap.other_matches) or (
        bool(added) and unknown / len(added) > UNKNOWN_ACTOR_SHARE)
    return ScopeReport(
        sprint=sprint, grace_hours=grace, cutoff=cutoff, baseline_count=len(baseline),
        baseline_points=baseline_points, baseline_unestimated=sum(1 for e in base_est if e is None),
        now_count=len(still), now_points=sum(i.estimate or 0 for i in still),
        added=tuple(sorted(added, key=lambda a: (a.added_at, a.issue_key))),
        removed=tuple(sorted(removed, key=lambda r: (r.removed_at, r.issue_key))),
        in_grace=tuple(sorted((f.in_grace for f in fates.values() if f.in_grace), key=lambda a: a.added_at)),
        net_change_pct=pct, severity=severity(pct), by_person=_by_person(added), by_day=_by_day(added),
        no_history=no_history, undetermined=undetermined,
        estimates_current=not any(e.kind == "estimate_changed" for e in snap.events),
        confidence=confidence, needs_human=needs_human,
    )


def severity(pct: float | None) -> str:
    if pct is None:
        return "n/a"
    if pct < SCOPE_MODERATE_PCT:
        return "none"
    return "moderate" if pct <= SCOPE_HIGH_PCT else "high"


def classify(issue: SprintIssue, events: Sequence[HistoryEvent], starts_at: datetime, cutoff: datetime,
             late_from: datetime) -> Fate:
    members = [e for e in events if e.kind in _MEMBERSHIP]
    before = [e for e in members if e.at <= cutoff]
    after = [e for e in members if e.at > cutoff]
    created = issue.created_at
    if before:
        in_at_cutoff: bool | None = before[-1].kind == "added_to_sprint"
    elif after:
        in_at_cutoff = after[0].kind == "removed_from_sprint" and (created is None or created <= cutoff)
    else:
        in_at_cutoff = None if created is None else created <= cutoff
    in_now = members[-1].kind == "added_to_sprint" if members else issue.in_sprint_now
    if in_at_cutoff is None:
        return Fate("undetermined")
    if in_at_cutoff:
        return _baseline_fate(issue, before, after, in_now, starts_at)
    adds = [e for e in after if e.kind == "added_to_sprint"]
    if adds:
        last = adds[-1]
        item = _added(issue, last.at, last.actor, "history", late_from,
                      from_backlog=created is not None and created <= cutoff)
    elif created is not None and created > cutoff and not members:
        item = _added(issue, created, issue.creator, "created_after_start", late_from)
    else:
        return Fate("gone")  # left the sprint before the cutoff and never came back
    if not in_now:
        removals = [e for e in after if e.kind == "removed_from_sprint" and e.at >= item.added_at]
        item = replace(item, removed_at=removals[-1].at if removals else None)
    return Fate("added", item=item)


def _baseline_fate(issue: SprintIssue, before: Sequence[HistoryEvent], after: Sequence[HistoryEvent],
                   in_now: bool, starts_at: datetime) -> Fate:
    grace = _grace_item(issue, before, starts_at)
    if in_now:
        return Fate("baseline", in_grace=grace)
    removals = [e for e in after if e.kind == "removed_from_sprint"]
    if not removals:
        return Fate("undetermined")  # out of the sprint now, but no dated removal to show
    last = removals[-1]
    return Fate("removed", removed=RemovedItem(issue.key, issue.title, issue.url, issue.estimate, last.at,
                                               _actor(last.actor)), in_grace=grace)


def _grace_item(issue: SprintIssue, before: Sequence[HistoryEvent], starts_at: datetime) -> AddedItem | None:
    """A baseline issue that joined between the start and the cutoff: shown as planning grace."""
    if before:
        last = before[-1]
        if last.at > starts_at:
            return _added(issue, last.at, last.actor, "history", None)
        return None
    if issue.created_at is not None and issue.created_at > starts_at:
        return _added(issue, issue.created_at, issue.creator, "created_after_start", None)
    return None


def _added(issue: SprintIssue, at: datetime, actor: str, source: str, late_from: datetime | None,
           from_backlog: bool = False) -> AddedItem:
    who = _actor(actor)
    return AddedItem(issue_key=issue.key, title=issue.title, url=issue.url, estimate=issue.estimate, added_at=at,
                     added_by=who, source=source, assignee=issue.assignee,
                     late=late_from is not None and at >= late_from, from_backlog=from_backlog,
                     by_automation=who != UNKNOWN and is_bot(who))


def _actor(actor: str) -> str:
    return actor.strip() if actor and actor.strip() else UNKNOWN


def estimate_at(issue: SprintIssue, events: Sequence[HistoryEvent], cutoff: datetime) -> float | None:
    """The estimate in force at the cutoff when the history has estimate changes, else the current one."""
    changes = [e for e in events if e.kind == "estimate_changed"]
    before = [e for e in changes if e.at <= cutoff]
    if before:
        return before[-1].estimate_to
    if changes:
        return changes[0].estimate_from
    return issue.estimate


def _mark_swaps(added: list[AddedItem], removed: list[RemovedItem]) -> tuple[list[AddedItem], list[RemovedItem]]:
    swapped_a: set[str] = set()
    swapped_r: set[str] = set()
    for a in added:
        for r in removed:
            if (a.added_by != UNKNOWN and a.added_by == r.removed_by and a.estimate is not None
                    and r.estimate is not None and abs(a.added_at - r.removed_at) <= SWAP_WINDOW
                    and abs(a.estimate - r.estimate) <= SWAP_POINTS):
                swapped_a.add(a.issue_key)
                swapped_r.add(r.issue_key)
    return ([replace(a, likely_swap=a.issue_key in swapped_a) for a in added],
            [replace(r, likely_swap=r.issue_key in swapped_r) for r in removed])


def _by_person(added: Sequence[AddedItem]) -> tuple[tuple[str, int, float], ...]:
    people: dict[str, tuple[int, float]] = {}
    for a in added:
        count, points = people.get(a.added_by, (0, 0.0))
        people[a.added_by] = (count + 1, points + (a.estimate or 0))
    return tuple((p, c, pts) for p, (c, pts) in sorted(people.items(), key=lambda kv: kv[0].casefold()))


def _by_day(added: Sequence[AddedItem]) -> tuple[tuple[str, int], ...]:
    days: dict[str, int] = {}
    for a in added:
        day = a.added_at.date().isoformat()
        days[day] = days.get(day, 0) + 1
    return tuple(sorted(days.items()))


def _events_by_key(events: Sequence[HistoryEvent]) -> dict[str, tuple[HistoryEvent, ...]]:
    out: dict[str, list[HistoryEvent]] = {}
    for e in sorted(events, key=lambda e: e.at):
        out.setdefault(e.issue_key, []).append(e)
    return {k: tuple(v) for k, v in out.items()}


def _confidence(snap: Snapshot, no_history: Sequence[str], undetermined: Sequence[str]) -> str:
    if snap.history_missing or (snap.issues and not snap.history_keys and not snap.events):
        return "low"
    if no_history or undetermined or snap.history_capped or snap.history_skipped or snap.partial:
        return "medium"
    return "high"
