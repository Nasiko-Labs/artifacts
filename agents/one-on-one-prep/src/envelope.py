"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from dataclasses import dataclass

HIGH_MIN_VERIFIED = 3
LOW_MAX_VERIFIED = 2


@dataclass(frozen=True)
class Facts:
    has_notes: bool
    has_updates: bool
    mixed: bool
    verified: int            # verified commitments + accomplishments
    total_verified: int      # every verified item
    fallback: bool           # the deadline fallback ran (carry-overs unassessed)
    sensitive_included: bool


def confidence(f: Facts) -> str:
    both = f.has_notes and f.has_updates and not f.mixed
    if f.fallback or f.total_verified < LOW_MAX_VERIFIED or not (f.has_notes and (f.has_updates or f.mixed)):
        return "low"
    return "high" if both and f.verified >= HIGH_MIN_VERIFIED else "medium"


def summary(points: list[dict], questions: list[dict], recognize: list[dict]) -> str:
    carry = sum(1 for p in points if p.get("status"))
    yours = sum(1 for p in points if p.get("flag") == "your commitment")
    return (f"{len(points)} talking point{'s' if len(points) != 1 else ''} ({carry} carry-over"
            f"{'s' if carry != 1 else ''}, {yours} yours), {len(questions)} question{'s' if len(questions) != 1 else ''}"
            f", {len(recognize)} thing{'s' if len(recognize) != 1 else ''} to recognize")


def build(points: list[dict], questions: list[dict], recognize: list[dict], carry_overs: list[dict], facts: Facts,
          left_out: dict, header: dict) -> dict:
    level = confidence(facts)
    yours = any(p.get("flag") == "your commitment" for p in points)
    return {"summary": summary(points, questions, recognize), "items": points, "talking_points": points,
            "questions": questions, "recognize": recognize, "carry_overs": carry_overs, "left_out": dict(left_out),
            "confidence": level, "needs_human": facts.sensitive_included or yours or level == "low", **header}


def refresh(env: dict, points: list[dict] | None = None, questions: list[dict] | None = None) -> dict:
    """A new envelope after a zero-call follow-up (add topic, drop)."""
    p = env["talking_points"] if points is None else points
    q = env["questions"] if questions is None else questions
    yours = any(x.get("flag") == "your commitment" for x in p)
    sensitive = bool(env.get("sensitive_included"))
    return {**env, "talking_points": p, "items": p, "questions": q, "summary": summary(p, q, env["recognize"]),
            "needs_human": sensitive or yours or env["confidence"] == "low"}
