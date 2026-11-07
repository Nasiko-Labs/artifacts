"""Pure: the evidence score, computed in code only from evidence (never from seniority or performance).

    score = sum(weight(kind) x 0.5 ** (age_days / 180)) + 1.0 if the evidence spans >= 2 sources

Weights: commit 1.0, pr 1.5, review 1.0, answer 2.0, doc_author 1.5, doc_edit 0.5. A person is `weak`
with fewer than 2 evidence items or nothing newer than 365 days. Also the evidence lines shown for a
person: grouped (all commits to one file are one line), best group first."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from identity import Person
from models import Evidence

WEIGHTS: Mapping[str, float] = {"commit": 1.0, "pr": 1.5, "review": 1.0, "answer": 2.0, "doc_author": 1.5,
                                "doc_edit": 0.5}
HALF_LIFE_DAYS = 180
BREADTH_BONUS = 1.0
BREADTH_SOURCES = 2
RECENT_DAYS = 365
MIN_ITEMS = 2


@dataclass(frozen=True)
class Group:
    """One evidence line: every item of one kind on one subject."""

    kind: str
    subject: str
    items: tuple[Evidence, ...]
    score: float

    @property
    def latest(self) -> Evidence:
        return max(self.items, key=lambda e: (e.day, bool(e.link)))


@dataclass(frozen=True)
class Scored:
    person: Person
    score: float
    breadth: int
    weak: bool
    groups: tuple[Group, ...]
    by_kind: Mapping[str, float]


def recency(day: date, today: date) -> float:
    age = max((today - day).days, 0)
    return 0.5 ** (age / HALF_LIFE_DAYS)


def item_score(item: Evidence, today: date) -> float:
    return WEIGHTS[item.kind] * recency(item.day, today)


def score_person(person: Person, today: date) -> Scored:
    by_kind: dict[str, float] = {}
    for item in person.evidence:
        by_kind = {**by_kind, item.kind: by_kind.get(item.kind, 0.0) + item_score(item, today)}
    breadth = len(person.sources)
    bonus = BREADTH_BONUS if breadth >= BREADTH_SOURCES else 0.0
    recent = any((today - e.day).days <= RECENT_DAYS for e in person.evidence)
    weak = len(person.evidence) < MIN_ITEMS or not recent
    parts = {k: round(v, 2) for k, v in by_kind.items()}  # the total is the sum of the parts shown
    return Scored(person, round(sum(parts.values()) + bonus, 2), breadth, weak, _groups(person.evidence, today),
                  parts)


def rank(people: Sequence[Person], today: date) -> tuple[Scored, ...]:
    """Strongest first; a tie goes to the person with more evidence, then by name (stable output)."""
    scored = [score_person(p, today) for p in people if p.evidence]
    return tuple(sorted(scored, key=lambda s: (-s.score, -len(s.person.evidence), s.person.name.casefold())))


def _groups(items: Sequence[Evidence], today: date) -> tuple[Group, ...]:
    buckets: dict[tuple[str, str], list[Evidence]] = {}
    for item in items:
        subject = item.detail if item.kind in ("commit", "answer", "doc_author", "doc_edit") else ""
        buckets.setdefault((item.kind, subject), []).append(item)
    groups = [Group(kind, subject, tuple(sorted(g, key=lambda e: e.day, reverse=True)),
                    sum(item_score(e, today) for e in g)) for (kind, subject), g in buckets.items()]
    return tuple(sorted(groups, key=lambda g: (-g.score, g.kind, g.subject)))
