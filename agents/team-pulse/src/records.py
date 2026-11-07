"""Checked activity records and the person they belong to. Frozen: built once by checks.py, never edited.

Times are aware UTC datetimes. Every record carries the URL a tool returned for it, which is the
evidence link shown to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Identity:
    """How a tool named someone: any of handle, display name, email (as returned)."""

    handle: str = ""
    name: str = ""
    email: str = ""

    def known(self) -> tuple[tuple[str, str], ...]:
        return tuple((k, v.strip()) for k, v in (("handle", self.handle), ("name", self.name),
                                                 ("email", self.email)) if v and v.strip())

    def aliases(self) -> frozenset[str]:
        values = {self.handle, self.name, self.email}
        if "@" in self.email:
            values.add(self.email.split("@", 1)[0])
        return frozenset(v.strip().casefold() for v in values if v and v.strip())


@dataclass(frozen=True)
class Person:
    """One team member. `label` is shown; `aliases` (casefolded) match records to them."""

    label: str
    aliases: frozenset[str]
    tz: ZoneInfo | None = None
    known: tuple[tuple[str, str], ...] = ()  # (kind, value as a tool wrote it), for argument replay


@dataclass(frozen=True)
class ReviewRequest:
    reviewer: Identity
    requested_at: datetime


@dataclass(frozen=True)
class Review:
    reviewer: Identity
    submitted_at: datetime
    url: str


@dataclass(frozen=True)
class PullRequest:
    id: str
    url: str
    author: Identity
    created_at: datetime
    merged_at: datetime | None = None
    review_requests: tuple[ReviewRequest, ...] = ()
    reviews: tuple[Review, ...] = ()


@dataclass(frozen=True)
class Commit:
    sha: str
    url: str
    author: Identity
    authored_at: datetime


@dataclass(frozen=True)
class Issue:
    key: str
    url: str
    assignee: Identity
    status: str
    status_since: datetime
    last_comment_at: datetime | None = None
    last_comment_url: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ChatActivity:
    """Message timestamps only; message text is never kept."""

    person: str  # the Person.label the chat step reported for
    timestamps: tuple[datetime, ...]
    total_reported: int | None = None
    tz: ZoneInfo | None = None
    sampled: bool = False  # the tool reported more messages than were transcribed


@dataclass(frozen=True)
class Activity:
    """Everything one turn pulled, after checks."""

    prs: tuple[PullRequest, ...] = ()
    commits: tuple[Commit, ...] = ()
    issues: tuple[Issue, ...] = ()
    chat: tuple[ChatActivity, ...] = ()
    caps_hit: tuple[str, ...] = ()


@dataclass(frozen=True)
class Signal:
    """A pattern raised by a fixed rule. `values` are already-formatted numbers for the template."""

    kind: str
    person: str | None  # None for a team-level signal
    values: dict = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
