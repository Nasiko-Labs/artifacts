"""Pure: the review as data (kept in the conversation state for follow-ups and the write) and the
code-computed `confidence` and `needs_human`."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields

from align import Criterion
from verify import Comment

DROPS_FOR_HIGH = 2


@dataclass(frozen=True)
class Review:
    owner: str = ""
    repo: str = ""
    number: str = ""
    title: str = ""
    url: str = ""
    ticket: str = ""                 # the key actually read ("" when none)
    ticket_title: str = ""
    other_refs: tuple[str, ...] = ()
    criteria: tuple[dict, ...] = ()
    comments: tuple[dict, ...] = ()
    label: str = "no_ticket"
    coverage: str = "0/0"
    scope_creep: tuple[str, ...] = ()
    scope_percent: int = 0
    similar: tuple[dict, ...] = ()   # {"pr": "owner/repo#N", "overlap": 0.5, "title": ...}
    summary: str = ""
    notes: tuple[str, ...] = ()
    reviewed: tuple[str, ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()
    not_reviewed: tuple[str, ...] = ()
    dropped: int = 0
    focus: str = ""
    pasted: bool = False
    chunks_ok: bool = True
    searched: tuple[str, ...] = field(default=())   # what was looked at for a not-found criterion

    @property
    def repo_full(self) -> str:
        return f"{self.owner}/{self.repo}" if self.owner and self.repo else ""

    @property
    def pr_label(self) -> str:
        return f"{self.repo_full}#{self.number}" if self.number else "the pasted diff"

    def criteria_objs(self) -> tuple[Criterion, ...]:
        return tuple(Criterion.from_dict(c) for c in self.criteria)

    def comment_objs(self) -> tuple[Comment, ...]:
        return tuple(Comment.from_dict(c) for c in self.comments)

    @property
    def confidence(self) -> str:
        if self.pasted or self.label == "unclear":
            return "low"
        gaps = bool(self.notes or self.not_reviewed or not self.chunks_ok)
        if self.ticket and not gaps and self.dropped <= DROPS_FOR_HIGH:
            return "high"
        return "medium"

    @property
    def needs_human(self) -> bool:
        blocker = any(c.get("severity") == "blocker" for c in self.comments)
        return blocker or self.label in ("misaligned", "partial") or self.confidence == "low"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["skipped"] = [list(x) for x in self.skipped]
        return data

    @staticmethod
    def from_dict(data: object) -> Review | None:
        if not isinstance(data, dict) or "label" not in data:
            return None
        values = {}
        for f in fields(Review):
            if f.name not in data:
                continue
            v = data[f.name]
            if f.name == "skipped":
                v = tuple(tuple(x) for x in v or ())
            elif isinstance(v, list):
                v = tuple(v)
            values[f.name] = v
        return Review(**values)
