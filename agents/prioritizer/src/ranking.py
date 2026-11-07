"""Pure: urgency, effort, score, order, pins and the drop/delegate rule, all computed in code from the stored
tasks and ratings, so any re-weight or re-date is a zero-call recompute."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

import dates
import estimates
import scoring
from checks import RUBRIC_LABELS, safe_delegate

QUICK_WIN_MIN_IMPACT = 3
QUICK_WIN_MAX_EFFORT = 2
DELEGATE_MAX_IMPACT = 3
DELEGATE_MAX_EFFORT = 4
NEVER_DROP_URGENCY = 4


@dataclass(frozen=True)
class Row:
    rank: int
    id: str
    text: str
    impact: int
    why: str
    urgency: int
    effort: int
    effort_estimated: bool
    due: date | None
    due_phrase: str | None
    days_left: int | None
    blocking: bool
    score: int
    pinned: bool
    action: str | None
    delegate_to: str | None
    action_reason: str
    reason: str
    order: int
    defaulted: bool


def _days_text(days: int) -> str:
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "today"
    return f"in {days} day{'s' if days != 1 else ''}"


def _urgency_text(urgency: int, due: dates.Due, days: int | None, blocking: bool) -> str:
    if due.date is not None:
        lead = f"Due {due.phrase} ({_days_text(days)})"
        return f"{lead}{', blocking' if blocking and urgency == dates.URGENT_FLOOR else ''}, so urgency {urgency}"
    if blocking:
        return f"Blocking, so urgency {urgency}"
    if due.urgent:
        return f"Due {due.phrase.upper() if due.phrase.casefold() == 'asap' else due.phrase}, so urgency {urgency}"
    return f"No deadline, so urgency {urgency}"


def _action(impact: int, urgency: int, effort: int, blocking: bool, rating: dict, text: str):
    protected = urgency >= NEVER_DROP_URGENCY or blocking
    if not protected and (impact <= 1 or (impact <= 2 and urgency <= 1)):
        reason = "low impact" + (", no deadline" if urgency <= 1 else "")
        return "drop", None, reason
    if rating.get("delegable") and impact <= DELEGATE_MAX_IMPACT and effort <= DELEGATE_MAX_EFFORT:
        return "delegate", safe_delegate(rating.get("delegate_to"), text) or "someone else", "someone else can do it as well"
    return None, None, ""


def _row(task: dict, rating: dict, order: int, weights, as_of: date) -> Row:
    text = task["text"]
    urgency, due, blocking = dates.urgency(text, as_of)
    days = dates.days_left(due.date, as_of)
    estimate = estimates.parse(text)
    effort = estimate.effort if estimate else int(rating.get("effort") or 3)
    impact = int(rating.get("impact") or 3)
    why = rating.get("impact_why") or RUBRIC_LABELS.get(impact, RUBRIC_LABELS[3])
    reason = (f"Impact {impact}: {why}. {_urgency_text(urgency, due, days, blocking)}. "
              f"Effort {effort}{' (your estimate)' if estimate else ''}.")
    action, delegate_to, action_reason = _action(impact, urgency, effort, blocking, rating, text)
    return Row(0, task["id"], text, impact, why, urgency, effort, estimate is not None, due.date, due.phrase, days,
               blocking, scoring.score(impact, urgency, effort, weights), False, action, delegate_to, action_reason,
               reason, order, bool(rating.get("defaulted")))


def _key(row: Row):
    return (-row.score, row.due or date.max, row.effort, row.order)


def compute(tasks: Sequence[dict], ratings: dict, goals: Sequence[str], preset: str, pins: Sequence[str],
            as_of: date) -> list[Row]:
    """Rows in rank order: pinned tasks first (most recent pin on top), then by score with tie-breaks on the
    earlier due date, the lower effort, then input order. `goals` are accepted for symmetry; the reasons were
    already grounded against them when the ratings were stored."""
    weights = scoring.weights_for(preset)
    rows = [_row(t, ratings.get(t["id"], {}), n, weights, as_of) for n, t in enumerate(tasks)]
    by_id = {r.id: r for r in rows}
    pinned = [by_id[p] for p in reversed(list(dict.fromkeys(pins))) if p in by_id]
    rest = sorted((r for r in rows if r.id not in {p.id for p in pinned}), key=_key)
    ordered = [_replace(r, pinned=True) for r in pinned] + rest
    return [_replace(r, rank=n) for n, r in enumerate(ordered, start=1)]


def _replace(row: Row, **changes) -> Row:
    return replace(row, **changes)


def quick_wins(rows: Sequence[Row]) -> list[Row]:
    return [r for r in rows if r.impact >= QUICK_WIN_MIN_IMPACT and r.effort <= QUICK_WIN_MAX_EFFORT]


def known_fraction(rows: Sequence[Row]) -> float:
    """Share of tasks with a due phrase or an estimate (what confidence rests on)."""
    if not rows:
        return 0.0
    return sum(1 for r in rows if r.due_phrase or r.effort_estimated) / len(rows)
