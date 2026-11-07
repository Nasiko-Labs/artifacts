"""Pure: rank code-host changes that landed near the spike onset. Code ranks; the model only words.

lag_days = onset - date(change). Eligible when 0 <= lag_days <= 7. Score 3 when lag <= 1, 2 when <= 3, else 1;
+2 when the title matches (case-insensitive, whole word) the service name, the dimension value or the resource
family; +1 for a release or a workflow run. Top 3 by score, then most recent. Always "coincident with the onset,
not proven"; no eligible change → stated, never hidden.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Literal

MAX_LAG_DAYS = 7
LOOKAHEAD_DAYS = 1
TOP_CANDIDATES = 3
TITLE_BONUS = 2
KIND_BONUS = 1
_WORD = re.compile(r"[a-z0-9]+")
_GENERIC = frozenset({"aws", "amazon", "google", "azure", "cloud", "service", "services", "the", "and", "for",
                      "per", "usage", "second", "seconds", "hours", "hour", "requests", "request", "gb", "tb",
                      "mb", "box", "usd"})
# Words a change title may use for a resource family, keyed by a word of the service name.
RESOURCE_FAMILY: dict[str, tuple[str, ...]] = {
    "lambda": ("function", "functions", "batch", "retry", "retries", "concurrency", "invocations"),
    "functions": ("function", "batch", "retry", "retries", "concurrency"),
    "ec2": ("instance", "instances", "autoscaling", "nodes", "compute"),
    "compute": ("instance", "instances", "autoscaling", "nodes", "vm"),
    "s3": ("bucket", "storage", "upload", "uploads"),
    "storage": ("bucket", "storage", "upload", "uploads"),
    "rds": ("database", "db", "replica", "postgres", "mysql"),
    "sql": ("database", "db", "replica", "query", "queries"),
    "elasticache": ("cache", "redis", "memcached"),
    "cloudwatch": ("logs", "logging", "metrics", "log"),
    "logging": ("logs", "logging", "log"),
    "dynamodb": ("table", "capacity", "index"),
    "transfer": ("egress", "cdn", "download", "downloads"),
    "kubernetes": ("cluster", "nodes", "pods", "replicas"),
    "eks": ("cluster", "nodes", "pods", "replicas"),
}

ChangeKind = Literal["pr", "release", "run", "commit"]


@dataclass(frozen=True)
class Change:
    repo: str
    kind: ChangeKind
    id: str
    title: str
    author_login: str | None
    at: datetime
    link: str

    @property
    def day(self) -> date:
        return self.at.astimezone(UTC).date() if self.at.tzinfo else self.at.date()

    def to_dict(self) -> dict:
        return {**asdict(self), "at": self.at.isoformat()}

    @staticmethod
    def from_dict(data: dict) -> Change:
        return Change(repo=str(data["repo"]), kind=data["kind"], id=str(data["id"]), title=str(data["title"]),
                      author_login=data.get("author_login") or None, at=datetime.fromisoformat(str(data["at"])),
                      link=str(data["link"]))


@dataclass(frozen=True)
class Candidate:
    change: Change
    lag_days: int
    score: int
    title_match: bool


def match_words(service: str | None, dimension_value: str | None) -> frozenset[str]:
    words = set()
    for text in (service or "", dimension_value or ""):
        words |= {w for w in _WORD.findall(text.casefold()) if len(w) >= 2 and w not in _GENERIC}
    family = {f for w in words for f in RESOURCE_FAMILY.get(w, ())}
    return frozenset(words | family)


def title_matches(title: str, words: Iterable[str]) -> bool:
    return bool(set(_WORD.findall(title.casefold())) & set(words))


def score(change: Change, onset: date, words: frozenset[str]) -> Candidate | None:
    lag = (onset - change.day).days
    if lag < 0 or lag > MAX_LAG_DAYS:
        return None
    base = 3 if lag <= 1 else 2 if lag <= 3 else 1
    matched = title_matches(change.title, words)
    total = base + (TITLE_BONUS if matched else 0) + (KIND_BONUS if change.kind in ("release", "run") else 0)
    return Candidate(change, lag, total, matched)


def rank(changes: Sequence[Change], onset: date, words: frozenset[str]) -> tuple[Candidate, ...]:
    scored = [c for c in (score(ch, onset, words) for ch in _unique(changes)) if c is not None]
    ordered = sorted(scored, key=lambda c: (-c.score, -c.change.at.timestamp(), c.change.id))
    return tuple(ordered[:TOP_CANDIDATES])


def _unique(changes: Sequence[Change]) -> tuple[Change, ...]:
    seen: dict[tuple[str, str, str], Change] = {}
    for ch in changes:
        seen.setdefault((ch.repo, ch.kind, ch.id), ch)
    return tuple(seen.values())
