"""Pure: the twelve rules (area, id, title, blocking) and how a row is built."""

from __future__ import annotations

from collections.abc import Sequence

from config import ENG5_BLOCKING, SUP1_BLOCKING
from records import Row

RULES: tuple[tuple[str, str, str, bool], ...] = (
    ("engineering", "ENG-1", "No open urgent/high issues", True),
    ("engineering", "ENG-2", "Scope closed", False),
    ("engineering", "ENG-3", "Nothing blocked", True),
    ("engineering", "ENG-4", "Open issues have owners", False),
    ("engineering", "ENG-5", "Launch scope found", ENG5_BLOCKING),
    ("docs", "DOC-1", "Launch doc exists", True),
    ("docs", "DOC-2", "Launch doc is fresh", False),
    ("docs", "DOC-3", "Launch doc is finished", False),
    ("support", "SUP-1", "Support prepared", SUP1_BLOCKING),
    ("marketing", "MKT-1", "Marketing tasks exist", False),
    ("marketing", "MKT-2", "Nothing overdue", True),
    ("marketing", "MKT-3", "Tasks have owners and dates", False),
)
_RULE = {rule_id: (area, title, blocking) for area, rule_id, title, blocking in RULES}


def row(rule_id: str, status: str, detail: str = "", evidence: Sequence[str] = ()) -> Row:
    area, title, blocking = _RULE[rule_id]
    return Row(area, rule_id, title, status, blocking, tuple(evidence), detail)


def unknown(area: str, reason: str) -> tuple[Row, ...]:
    return tuple(row(r, "unknown", f"not read: {reason}") for a, r, _, _ in RULES if a == area)


def cite(ident: str, title: str, url: str | None) -> str:
    return f"{ident} {title}" + (f" ({url})" if url else "")
