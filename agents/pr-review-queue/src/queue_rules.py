"""Pure: staleness, blockers and priority for each open pull request, all computed in code.

`blocked_by` kinds, in rule order (a pull request may have several):
  merge_conflict, failing_checks, changes_requested, awaiting_reviewer, awaiting_first_review, ready_to_merge.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from facts import (
    PRFacts,
    days_between,
    is_fresh,
    last_activity_at,
    last_push_at,
    latest_reviews,
)
from records import parse_time

KINDS = ("merge_conflict", "failing_checks", "changes_requested", "awaiting_reviewer", "awaiting_first_review",
         "ready_to_merge")
NOBODY = "nobody assigned"
AWAITING_BONUS, READY_BONUS = 2, 3
LINES_PER_POINT, MAX_SIZE_POINTS = 100, 3
TEAM_PREFIX = "team "

DETAIL = {
    "merge_conflict": "has a merge conflict",
    "failing_checks": "checks are failing",
    "changes_requested": "changes were requested and nothing has been pushed since",
    "awaiting_reviewer": "review requested, no review since the last push",
    "awaiting_first_review": "no reviewer assigned and no review yet",
    "ready_to_merge": "approved and ready to merge",
}


@dataclass(frozen=True)
class Blocked:
    repo: str
    number: int
    kind: str
    who: str
    since_days: int
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Assessed:
    """One pull request with every number the queue needs."""

    facts: PRFacts
    age_days: int
    quiet_days: int
    blocked: tuple[Blocked, ...]
    priority: int

    @property
    def waiting_on(self) -> str:
        awaiting = next((b for b in self.blocked if b.kind.startswith("awaiting")), None)
        first = awaiting or (self.blocked[0] if self.blocked else None)
        return first.who if first else self.facts.pr.author or "unknown"


def assess(f: PRFacts, now: datetime) -> Assessed:
    created = parse_time(f.pr.created_at)
    age = days_between(now, created)
    quiet = days_between(now, last_activity_at(f))
    blocked = tuple(blockers(f, now, age))
    return Assessed(f, age, quiet, blocked, priority(quiet, blocked, f.pr.changed_lines))


def priority(quiet: int, blocked: Sequence[Blocked], changed_lines: int) -> int:
    kinds = {b.kind for b in blocked}
    size = min(MAX_SIZE_POINTS, changed_lines // LINES_PER_POINT)
    return (quiet + (AWAITING_BONUS if "awaiting_reviewer" in kinds else 0)
            + (READY_BONUS if "ready_to_merge" in kinds else 0) + size)


def is_stale(a: Assessed, stale_days: int, include_drafts: bool) -> bool:
    return a.quiet_days >= stale_days and (include_drafts or not a.facts.pr.draft)


def blockers(f: PRFacts, now: datetime, age: int) -> list[Blocked]:
    pr, author = f.pr, f.pr.author or "unknown"
    push = last_push_at(f)
    since_push = days_between(now, push) if push else age
    out: list[Blocked] = []

    def add(kind: str, who: str, since: int) -> None:
        out.append(Blocked(f.repo, int(pr.number), kind, who, since, DETAIL[kind]))

    if pr.conflict:
        add("merge_conflict", author, since_push)
    if f.checks == "failing":
        add("failing_checks", author, since_push)
    if not f.reviews_read:
        return out  # reviews unread: only conflicts and checks are known (the coverage says so)
    latest = latest_reviews(f)
    changes = [r for r in latest.values() if r.state == "CHANGES_REQUESTED"]
    blocking = [r for r in changes if not _pushed_after(push, r.submitted_at)]
    if blocking:
        oldest = min(parse_time(r.submitted_at) for r in blocking)
        add("changes_requested", author, days_between(now, oldest))
    rereview = [r.author for r in changes if r not in blocking]
    awaited = [p for p in dict.fromkeys((*pr.requested, *rereview))
               if p != pr.author and not (p in latest and is_fresh(latest[p], push))]
    for person in awaited:
        add("awaiting_reviewer", person, since_push)
    for team in pr.teams:
        add("awaiting_reviewer", TEAM_PREFIX + team, since_push)
    if not pr.requested and not pr.teams and not latest:
        add("awaiting_first_review", NOBODY, age)
    approvals = [r for r in latest.values() if r.state == "APPROVED"]
    if approvals and not changes and f.checks in ("passing", "none") and not pr.conflict and not awaited:
        newest = max(parse_time(r.submitted_at) for r in approvals)
        add("ready_to_merge", author, days_between(now, newest))
    return out


def _pushed_after(push: datetime | None, when: str) -> bool:
    moment = parse_time(when)
    return push is not None and moment is not None and push > moment


def ordered(stale: Sequence[Assessed]) -> list[Assessed]:
    return sorted(stale, key=lambda a: (-a.priority, -a.quiet_days, a.facts.repo, int(a.facts.pr.number)))
