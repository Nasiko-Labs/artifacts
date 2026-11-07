"""Pure: the list the user is shown, computed in code (sorting and overdue maths copied from the
promise-tracker agent). Open first, then unclear, then done; each by most overdue, then oldest sent.
Numbered 1..n and capped at 15; overdue = due before the as-of date and not done."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from promises import Promise

MAX_ITEMS = 15
_STATE_ORDER = {"open": 0, "unclear": 1, "done": 2}


@dataclass(frozen=True)
class Row:
    number: int
    promise: Promise
    days_overdue: int | None   # > 0 only when overdue


@dataclass(frozen=True)
class Ledger:
    rows: tuple[Row, ...]
    capped: int
    as_of: date

    @property
    def overdue(self) -> tuple[Row, ...]:
        rows = (r for r in self.rows if r.days_overdue)
        return tuple(sorted(rows, key=lambda r: (-(r.days_overdue or 0), r.number)))

    def count(self, state: str) -> int:
        return sum(1 for r in self.rows if r.promise.state == state)


def parse_day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def overdue_days(promise: Promise, as_of: date) -> int | None:
    """as_of − due in days (negative when not yet due); None without a due date."""
    due = parse_day(promise.due)
    return None if due is None else (as_of - due).days


def build(promises: Sequence[Promise], as_of: date) -> Ledger:
    ordered = sorted(promises, key=lambda p: _key(p, as_of))
    rows = tuple(Row(n, p, _days_overdue(p, as_of)) for n, p in enumerate(ordered[:MAX_ITEMS], start=1))
    return Ledger(rows=rows, capped=max(0, len(ordered) - MAX_ITEMS), as_of=as_of)


def _days_overdue(promise: Promise, as_of: date) -> int | None:
    days = overdue_days(promise, as_of)
    return days if days is not None and days > 0 and promise.state != "done" else None


def _key(promise: Promise, as_of: date) -> tuple:
    overdue = overdue_days(promise, as_of)
    sent = parse_day(promise.sent_on) or date.max
    return (_STATE_ORDER.get(promise.state, 1), overdue is None, -(overdue or 0), sent, promise.order)
