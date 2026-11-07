"""Checked records from the code host and the tracker. Frozen: built once by checks.py, never edited.

Times are aware UTC datetimes. Every record carries the URL a tool returned for it, which is the link
shown on its standup line. `to_json`/`from_json` keep records in the conversation checkpoint so a
follow-up ("only maya", "shorter") is answered without reading the tools again.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Identity:
    """How a tool named someone: any of handle, display name, email (as returned)."""

    handle: str = ""
    name: str = ""
    email: str = ""

    def is_empty(self) -> bool:
        return not (self.handle.strip() or self.name.strip() or self.email.strip())


@dataclass(frozen=True)
class Comment:
    body: str
    at: datetime | None = None


@dataclass(frozen=True)
class Review:
    reviewer: Identity
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
    submitted_at: datetime


@dataclass(frozen=True)
class PullRequest:
    url: str
    author: Identity
    created_at: datetime
    title: str = ""
    repo: str = ""
    number: int | None = None
    state: str = "open"  # open | merged | closed
    draft: bool = False
    merged_at: datetime | None = None
    last_push_at: datetime | None = None
    branch: str = ""
    checks: str = "unknown"  # failing | passing | unknown
    conflict: bool = False
    reviews: tuple[Review, ...] = ()
    comment: Comment | None = None

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}" if self.repo and self.number is not None else self.url


@dataclass(frozen=True)
class Commit:
    sha: str
    url: str
    author: Identity
    authored_at: datetime
    branch: str = ""
    message: str = ""


@dataclass(frozen=True)
class Issue:
    key: str
    url: str
    assignee: Identity
    status: str
    category: str  # done | started | blocked | other (computed in code)
    title: str = ""
    labels: tuple[str, ...] = ()
    completed_at: datetime | None = None
    updated_at: datetime | None = None
    blocked_by: tuple[str, ...] = ()
    comment: Comment | None = None


@dataclass(frozen=True)
class Records:
    prs: tuple[PullRequest, ...] = ()
    commits: tuple[Commit, ...] = ()
    issues: tuple[Issue, ...] = ()
    cuts: tuple[str, ...] = ()  # sources whose record cap was reached


def to_json(records: Records) -> dict:
    return _plain(asdict(records))


def from_json(raw: dict) -> Records:
    return Records(
        prs=tuple(_pr(p) for p in raw.get("prs", ())),
        commits=tuple(Commit(c["sha"], c["url"], Identity(**c["author"]), _time(c["authored_at"]), c.get("branch", ""),
                             c.get("message", "")) for c in raw.get("commits", ())),
        issues=tuple(_issue(i) for i in raw.get("issues", ())),
        cuts=tuple(raw.get("cuts", ())),
    )


def _pr(p: dict) -> PullRequest:
    reviews = tuple(Review(Identity(**r["reviewer"]), r["state"], _time(r["submitted_at"])) for r in p["reviews"])
    return PullRequest(
        url=p["url"], author=Identity(**p["author"]), created_at=_time(p["created_at"]), title=p["title"],
        repo=p["repo"], number=p["number"], state=p["state"], draft=p["draft"], merged_at=_time(p["merged_at"]),
        last_push_at=_time(p["last_push_at"]), branch=p["branch"], checks=p["checks"], conflict=p["conflict"],
        reviews=reviews, comment=_comment(p["comment"]))


def _issue(i: dict) -> Issue:
    return Issue(key=i["key"], url=i["url"], assignee=Identity(**i["assignee"]), status=i["status"],
                 category=i["category"], title=i["title"], labels=tuple(i["labels"]),
                 completed_at=_time(i["completed_at"]), updated_at=_time(i["updated_at"]),
                 blocked_by=tuple(i["blocked_by"]), comment=_comment(i["comment"]))


def _comment(raw: dict | None) -> Comment | None:
    return None if raw is None else Comment(raw["body"], _time(raw["at"]))


def _time(raw: str | None) -> datetime | None:
    return None if raw is None else datetime.fromisoformat(raw)


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value
