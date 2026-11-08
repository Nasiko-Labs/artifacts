"""Pure: `confidence` and `needs_human`, computed in code from what the checks saw (never from the model),
and the envelope every reply carries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from records import BugMatch
from risk import Assessed

_DROP_SHARE_LOW = 0.2


@dataclass(frozen=True)
class Checks:
    desk_read: bool
    tracker_read: bool
    capped: bool = False
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False
    comments_missing: int = 0
    ambiguous: bool = False


def confidence(c: Checks) -> str:
    share = c.dropped / c.extracted if c.extracted else 0.0
    if not c.desk_read or share > _DROP_SHARE_LOW or c.salvaged:
        return "low"
    if not c.tracker_read or c.capped or c.dropped or c.comments_missing:
        return "medium"
    return "high"


def needs_human(c: Checks, rows: Sequence[Assessed]) -> bool:
    return confidence(c) == "low" or c.ambiguous or any(r.risk_level == "high" for r in rows)


def row_dict(row: Assessed) -> dict:
    data = asdict(row)
    data["factors"] = [asdict(f) for f in row.factors]
    return data


def build(summary: str, rows: Sequence[Assessed], matches: Sequence[BugMatch], c: Checks,
          notes: Sequence[str] = ()) -> dict:
    at_risk = [row_dict(r) for r in rows]
    return {"summary": summary, "items": at_risk, "confidence": confidence(c), "needs_human": needs_human(c, rows),
            "at_risk": at_risk, "bug_matches": [_match(m) for m in matches], "drafts": [], "notes": list(notes)}


def _match(m: BugMatch) -> dict:
    return {"ticket_id": m.ticket_id, "issue_id": m.issue_id, "title": m.title, "url": m.url, "state": m.state,
            "score": m.score}
