"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `review_markdown`,
`evidence_gaps`, `bias_flags`, `left_out`). `confidence` is computed here from the checks; `needs_human` is
always true: the draft is a starting point for the manager, never a decision."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from evidence import Evidence
from verify import Statement

LOW_MAX_GAPS = 2
LOW_MIN_USABLE = 8


@dataclass(frozen=True)
class Review:
    person: str
    role: str
    period: str                                   # "2026-01-01 to 2026-06-30" or ""
    evidence: tuple[Evidence, ...]
    statements: tuple[Statement, ...] = ()
    gaps: tuple[dict, ...] = ()
    flags: tuple[dict, ...] = ()
    left_out: tuple[dict, ...] = ()
    dropped: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()
    fallback: bool = False

    @property
    def usable(self) -> list[Evidence]:
        return [e for e in self.evidence if e.specific and e.tagged and not e.excluded]


def confidence(review: Review) -> str:
    if review.fallback or len(review.gaps) > LOW_MAX_GAPS or len(review.usable) < LOW_MIN_USABLE:
        return "low"
    if not review.gaps and not [f for f in review.flags if not f["note"]]:
        return "high"
    return "medium"


def summary_line(review: Review) -> str:
    if review.fallback:
        return (f"Evidence table for {review.person} (no prose): {len(review.usable)} usable of "
                f"{len(review.evidence)} items; {len(review.gaps)} gaps.")
    strengths = sum(1 for s in review.statements if s.section == "strengths")
    growth = sum(1 for s in review.statements if s.section == "growth")
    flags = sum(1 for f in review.flags if not f["note"])
    return (f"Draft review for {review.person}: {strengths} strengths, {growth} growth areas from "
            f"{len(review.usable)} usable of {len(review.evidence)} items; {len(review.gaps)} gaps; {flags} flags.")


def build(review: Review, markdown: str) -> dict:
    items = [s.to_dict() for s in review.statements]
    return {
        "summary": summary_line(review), "items": items, "review_markdown": markdown,
        "evidence_gaps": [dict(g) for g in review.gaps], "bias_flags": [dict(f) for f in review.flags],
        "left_out": [dict(x) for x in review.left_out], "confidence": confidence(review), "needs_human": True,
        "person": review.person, "period": review.period, "usable_items": len(review.usable),
        "total_items": len(review.evidence), "dropped_statements": len(review.dropped),
        "dropped": [dict(d) for d in review.dropped], "notes": list(review.notes),
    }


def by_id(evidence: Sequence[Evidence]) -> dict[str, Evidence]:
    return {e.id: e for e in evidence}


def to_state(review: Review) -> dict:
    return {"person": review.person, "role": review.role, "period": review.period,
            "evidence": [e.to_dict() for e in review.evidence], "statements": [s.to_dict() for s in review.statements],
            "gaps": [dict(g) for g in review.gaps], "flags": [dict(f) for f in review.flags],
            "left_out": [dict(x) for x in review.left_out], "dropped": [dict(d) for d in review.dropped],
            "notes": list(review.notes), "fallback": review.fallback}


def from_state(data: dict | None) -> Review | None:
    if not data:
        return None
    return Review(person=data["person"], role=data["role"], period=data["period"],
                  evidence=tuple(Evidence.from_dict(e) for e in data["evidence"]),
                  statements=tuple(Statement.from_dict(s) for s in data["statements"]),
                  gaps=tuple(data["gaps"]), flags=tuple(data["flags"]), left_out=tuple(data["left_out"]),
                  dropped=tuple(data["dropped"]), notes=tuple(data["notes"]), fallback=bool(data["fallback"]))
