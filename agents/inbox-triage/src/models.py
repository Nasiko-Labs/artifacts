"""Pure data: a mail thread as read from a tool (or a paste), and the triaged thread shown to the user.

Both round-trip through JSON-safe dicts so the checkpointer can keep them across turns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

Bucket = Literal["urgent", "reply", "ignore"]
BUCKETS: tuple[Bucket, ...] = ("urgent", "reply", "ignore")


@dataclass(frozen=True)
class Mail:
    """One thread, newest message first-class. `text` is every message's subject and body as the tool
    returned it: quotes are verified against it. `last_text` is the newest message's own text."""

    id: str
    sender: str
    subject: str
    date: str | None  # ISO-8601 UTC of the newest message, or None when unknown
    to: str = ""
    cc: str = ""
    last_text: str = ""
    text: str = ""
    labels: tuple[str, ...] = ()
    list_unsubscribe: bool = False
    precedence_bulk: bool = False
    link: str | None = None
    msg_count: int = 1
    last_from_me: bool = False
    truncated: bool = False

    def to_dict(self) -> dict:
        return {**asdict(self), "labels": list(self.labels)}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Mail:
        return Mail(**{**dict(data), "labels": tuple(data.get("labels") or ())})


@dataclass(frozen=True)
class Thread:
    """A triaged thread. `source` says who decided the bucket: a code rule, the model, or the user."""

    id: str
    sender: str
    subject: str
    date: str | None
    bucket: Bucket
    reason: str
    evidence: str | None = None
    deadline: str | None = None
    age_days: float | None = None
    link: str | None = None
    source: Literal["rule", "model", "fallback", "user"] = "rule"
    vip: bool = False
    bulk: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Thread:
        return Thread(**dict(data))

    def moved(self, bucket: Bucket) -> Thread:
        return replace(self, bucket=bucket, source="user", reason=f"moved to {bucket} by you ({self.reason})")

    def public(self) -> dict:
        """The envelope form (catalog field names)."""
        return {"id": self.id, "from": self.sender, "subject": self.subject, "date": self.date,
                "bucket": self.bucket, "reason": self.reason, "evidence": self.evidence,
                "deadline": self.deadline, "age_days": self.age_days, "link": self.link}


@dataclass(frozen=True)
class Checks:
    """What happened on the way to the buckets, counted in code; confidence is computed from it."""

    demoted: int = 0
    dropped: int = 0
    unclassified: int = 0
    truncated: int = 0
    capped: bool = False
    pasted: bool = False
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return {**asdict(self), "notes": list(self.notes)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Checks:
        raw = dict(data or {})
        return Checks(**{**raw, "notes": tuple(raw.get("notes") or ())})
