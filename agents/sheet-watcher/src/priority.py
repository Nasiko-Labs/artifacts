"""Pure: event priority and order, decided in code.

    date_overdue > required_blank > state_change into a blocked state > date_approaching > other state_change
    > new_row > removed_row > row_changed

New events come before repeats (an unchanged event already reported last time); ties keep sheet order."""

from __future__ import annotations

import re
from collections.abc import Iterable

from models import Event

BLOCKED = re.compile(r"^\s*(blocked|at risk|off track|delayed)\s*$", re.IGNORECASE)
_RANK = {"date_overdue": 8, "required_blank": 7, "blocked_change": 6, "date_approaching": 5, "state_change": 4,
         "new_row": 3, "removed_row": 2, "row_changed": 1}


def priority(event: Event) -> int:
    if event.type == "state_change" and BLOCKED.match(event.new or ""):
        return _RANK["blocked_change"]
    return _RANK[event.type]


def ranked(events: Iterable[Event], order: dict[str, int]) -> list[Event]:
    """Each event with its priority set, sorted: new before repeat, then priority, then sheet order."""
    scored = [e.model_copy(update={"priority": priority(e)}) for e in events]
    return sorted(scored, key=lambda e: (e.repeat, -e.priority, order.get(e.row_key, 10**9)))
