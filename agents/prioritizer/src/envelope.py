"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `ranked`, `drop_or_delegate`).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from ranking import Row, known_fraction

HIGH_MIN_KNOWN = 0.7
LOW_MAX_KNOWN = 0.3
CLOSE_CALL_POINTS = 3
CLOSE_CALL_TOP = 3


def ranked_item(row: Row) -> dict:
    return {"rank": row.rank, "task": row.text, "score": row.score, "reason": row.reason, "impact": row.impact,
            "urgency": row.urgency, "effort": row.effort, "due": row.due.isoformat() if row.due else None,
            "due_phrase": row.due_phrase, "pinned": row.pinned, "action": row.action}


def action_item(row: Row) -> dict:
    return {"task": row.text, "action": row.action, "delegate_to": row.delegate_to, "reason": row.action_reason}


def confidence(rows: Sequence[Row], goals: Sequence[str], defaulted: int, failed: int) -> str:
    known = known_fraction(rows)
    if failed or (not goals and known < LOW_MAX_KNOWN):
        return "low"
    if goals and known >= HIGH_MIN_KNOWN and not defaulted:
        return "high"
    return "medium"


def close_call(rows: Sequence[Row]) -> tuple[Row, Row] | None:
    """The first adjacent pair in the top 3 (unpinned) whose scores differ by fewer than 3 points."""
    top = [r for r in rows if not r.pinned][:CLOSE_CALL_TOP]
    for upper, lower in pairwise(top):
        if upper.score - lower.score < CLOSE_CALL_POINTS:
            return upper, lower
    return None


def needs_human(rows: Sequence[Row], level: str) -> bool:
    return level == "low" or close_call(rows) is not None


def build(rows: Sequence[Row], goals: Sequence[str], defaulted: int, failed: int) -> dict:
    ranked = [ranked_item(r) for r in rows]
    actions = [action_item(r) for r in rows if r.action]
    level = confidence(rows, goals, defaulted, failed)
    summary = (f"Ranked {len(rows)} tasks; top: {rows[0].text}. {len(actions)} to drop or delegate."
               if rows else "No tasks to rank.")
    return {"summary": summary, "items": ranked, "ranked": ranked, "drop_or_delegate": actions,
            "goals": list(goals), "confidence": level, "needs_human": needs_human(rows, level)}
