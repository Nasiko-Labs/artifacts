"""Checked evidence records. Frozen: built once by checks.py / pasted.py, never edited.

Times are aware UTC datetimes. Every record carries the link a tool returned for it (empty only for
evidence the user pasted, which is labelled as such)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Person:
    """One person. `label` is shown; `aliases` (casefolded) match records to them by whole words;
    `known` is (kind, value as a tool or the user wrote it), for argument replay."""

    label: str
    aliases: frozenset[str]
    known: tuple[tuple[str, str], ...] = ()
    named: bool = True  # False: inferred from a repository's merged pull requests


@dataclass(frozen=True)
class LinkedIssue:
    ref: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    title: str
    person: str  # Person.label
    merged_at: datetime | None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    labels: tuple[str, ...] = ()
    review_comments: int | None = None
    reviewers: tuple[str, ...] = ()
    linked: tuple[LinkedIssue, ...] = ()
    pasted: bool = False


@dataclass(frozen=True)
class Release:
    name: str
    notes: str


@dataclass(frozen=True)
class PraiseItem:
    person: str  # Person.label
    quote: str  # verbatim (truncated in code for display)
    author: str
    channel: str
    permalink: str
    ts: datetime | None
    pasted: bool = False


@dataclass(frozen=True)
class NotablePR:
    pr: PullRequest
    why: tuple[str, ...]  # rule ids: size, review, linked, label (or "pasted")
    reasons: tuple[str, ...]  # the same, as short factual phrases


@dataclass(frozen=True)
class ShippedItem:
    pr: PullRequest
    release: str = ""


@dataclass(frozen=True)
class PersonPacket:
    person: Person
    prs_merged: int
    notable: tuple[NotablePR, ...]
    shipped: tuple[ShippedItem, ...]
    praise: tuple[PraiseItem, ...]
    distinct_praisers: int
    gaps: tuple[str, ...]

    @property
    def empty(self) -> bool:
        return not (self.prs_merged or self.notable or self.praise)
