"""Checked records. Frozen: built once by checks.py from this turn's tool output, never edited.

Every string here appeared verbatim in the tool output it came from; dates are parsed in code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

STATUS_TYPES = ("backlog", "unstarted", "started", "completed", "canceled")
OPEN_TYPES = frozenset({"backlog", "unstarted", "started"})


@dataclass(frozen=True)
class Blocker:
    key: str
    status_type: str | None

    @property
    def is_open(self) -> bool:
        """Unknown counts as open: a blocking link with no state is still a blocking link."""
        return self.status_type not in ("completed", "canceled")


@dataclass(frozen=True)
class Issue:
    key: str
    url: str
    title: str
    team: str
    status: str
    status_type: str | None
    assignee: str | None
    created_at: date | None
    updated_at: date | None
    completed_at: date | None
    due: date | None
    labels: tuple[str, ...] = ()
    parent_key: str | None = None
    blocked_by: tuple[Blocker, ...] = ()
    last_comment_at: date | None = None
    updated_unparsed: bool = False

    @property
    def updated(self) -> date | None:
        """`updated` in the rules: the later of the last update and the last comment."""
        stamps = [d for d in (self.updated_at, self.last_comment_at) if d]
        return max(stamps) if stamps else None

    @property
    def open_blockers(self) -> tuple[Blocker, ...]:
        return tuple(b for b in self.blocked_by if b.is_open)


@dataclass(frozen=True)
class Channel:
    id: str
    name: str


@dataclass(frozen=True)
class PostItem:
    item: str
    reported_words: str
    ref: str | None = None
    owner: str | None = None
    hint: str | None = None  # the model's category, used only when no status word matches


@dataclass(frozen=True)
class Post:
    channel: str
    author: str
    at: datetime
    permalink: str
    quote: str
    items: tuple[PostItem, ...]


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    url: str
    start: date
    attendee_count: int | None = None
