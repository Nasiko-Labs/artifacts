"""Pure: one open pull request's facts as read this turn, and the dates derived from them in code.

Stored per conversation (JSON-safe), so a follow-up ("stale after 7 days", "who is overloaded")
recomputes the queue without reading the code host again.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from records import Comment, PullRequest, Review, parse_time

DAY_SECS = 86_400
# Bot logins differ by provider; matched as a name pattern (a tunable list, not a provider name).
_BOT = re.compile(r"(?:\[bot\]|-bot|_bot|bot\[\d+\])$", re.IGNORECASE)


def is_bot(login: str) -> bool:
    return bool(_BOT.search(login or ""))


@dataclass(frozen=True)
class PRFacts:
    repo: str
    pr: PullRequest
    reviews: tuple[Review, ...] = ()
    comments: tuple[Comment, ...] = ()
    commit_dates: tuple[str, ...] = ()
    checks: str = ""          # failing / pending / passing / none (read, no checks) / "" (not read)
    inspected: bool = False   # the per-PR reads ran (False: only the listing was read)
    reviews_read: bool = False  # False: review-based blockers are not computed

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.pr.number}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> PRFacts:
        pr = dict(data.get("pr") or {})
        pr["requested"] = tuple(pr.get("requested") or ())
        pr["teams"] = tuple(pr.get("teams") or ())
        return PRFacts(
            repo=str(data.get("repo") or ""), pr=PullRequest(**pr),
            reviews=tuple(Review(**r) for r in data.get("reviews") or ()),
            comments=tuple(Comment(**c) for c in data.get("comments") or ()),
            commit_dates=tuple(data.get("commit_dates") or ()), checks=str(data.get("checks") or ""),
            inspected=bool(data.get("inspected")), reviews_read=bool(data.get("reviews_read")))

    def with_detail(self, **changes: Any) -> PRFacts:
        return replace(self, **changes)


def days_between(now: datetime, moment: datetime | None) -> int:
    """Whole days from `moment` to `now` (floor), never negative."""
    if moment is None:
        return 0
    return max(0, int((now - moment).total_seconds() // DAY_SECS))


def last_push_at(f: PRFacts) -> datetime | None:
    dates = [d for d in (parse_time(x) for x in f.commit_dates) if d]
    return max(dates, default=None)


def last_activity_at(f: PRFacts) -> datetime | None:
    """Latest commit, review or comment by anyone who is not a bot. Without the per-PR reads, the
    listing's own update time stands in (the coverage line says so)."""
    if not f.inspected:
        return parse_time(f.pr.updated_at) or parse_time(f.pr.created_at)
    moments = [parse_time(x) for x in f.commit_dates]
    moments += [parse_time(r.submitted_at) for r in f.reviews if not is_bot(r.author)]
    moments += [parse_time(c.created_at) for c in f.comments if not is_bot(c.author)]
    found = [m for m in moments if m]
    return max(found, default=None) or parse_time(f.pr.created_at)


def latest_reviews(f: PRFacts) -> dict[str, Review]:
    """Each reviewer's latest review by time (the author and bots are not reviewers)."""
    latest: dict[str, Review] = {}
    for r in sorted(f.reviews, key=lambda r: r.submitted_at):
        if r.author and r.author != f.pr.author and not is_bot(r.author) and r.state != "PENDING":
            latest[r.author] = r
    return latest


def is_fresh(review: Review, push: datetime | None) -> bool:
    when = parse_time(review.submitted_at)
    return when is not None and (push is None or when > push)
