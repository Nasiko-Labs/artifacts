"""Pure: evidence strings, built only from export cells and the as-of date (never from model text).

    KEY-12 'Payments migration' is **Blocked** (updated 2026-10-02, 33 days ago; assignee unassigned);
    due 2026-10-15 (20 days overdue)
    Epic KEY-1 'Search v2': 3 of 11 children done (27%), 2 blocked, due 2026-11-12 (in 8 days)
"""

from __future__ import annotations

import re
from datetime import date

from dates import from_iso, plural
from rules import Facts

MAX_TITLE_CHARS = 80
MAX_CELL_CHARS = 40

_MARKER = re.compile(r"<<<\s*[A-Z_]{2,40}\s*>>>|>>>\s*END_[A-Z_]{2,40}\s*<<<", re.IGNORECASE)
_UNSAFE = re.compile(r"[|\n\r`<>*\[\]]+")


def clean(text: str, limit: int = MAX_TITLE_CHARS) -> str:
    """An export cell made safe for one markdown table cell: no pipes, line breaks, markup or fence markers."""
    plain = " ".join(_UNSAFE.sub(" ", _MARKER.sub("", text or "")).split())
    return plain if len(plain) <= limit else plain[: limit - 1].rstrip() + "…"


def ticket(t: dict, f: Facts, as_of: date) -> str:
    head = f"{clean(t['id'], MAX_CELL_CHARS)} '{clean(t['title'])}' is **{clean(t['status'], MAX_CELL_CHARS) or 'no status'}**"
    details = [d for d in (_updated(t, as_of), _assignee(t)) if d]
    text = head + (f" ({'; '.join(details)})" if details else "")
    due = _due(f.due, as_of)
    return f"{text}; {due}" if due else text


def epic(t: dict, f: Facts, as_of: date) -> str:
    pct = round(100 * f.pct_done)
    head = f"Epic {clean(t['id'], MAX_CELL_CHARS)} '{clean(t['title'])}': {f.n_done} of {f.n_children} children done"
    parts = [f"{head} ({pct}%)"]
    if f.n_blocked:
        parts.append(f"{f.n_blocked} blocked")
    if f.n_in_progress:
        parts.append(f"{f.n_in_progress} in progress")
    due = _due(f.due, as_of)
    if due:
        parts.append(due)
    if f.updated:
        parts.append(f"last update {f.updated.isoformat()} ({_ago((as_of - f.updated).days)})")
    return ", ".join(parts)


def child_line(t: dict) -> str:
    owner = t.get("assignee")
    who = f", {clean(owner, MAX_CELL_CHARS) or 'unassigned'}" if owner is not None else ""
    return f"{clean(t['id'], MAX_CELL_CHARS)} '{clean(t['title'])}': {clean(t['status'], MAX_CELL_CHARS)}{who}"


def _updated(t: dict, as_of: date) -> str:
    if "updated" in (t.get("unparsed") or ()):
        return "updated date unreadable"
    day = from_iso(t.get("updated"))
    return f"updated {day.isoformat()}, {_ago((as_of - day).days)}" if day else ""


def _assignee(t: dict) -> str:
    owner = t.get("assignee")
    if owner is None:  # the export has no assignee column
        return ""
    return f"assignee {clean(owner, MAX_CELL_CHARS) or 'unassigned'}"


def _due(due: date | None, as_of: date) -> str:
    if due is None:
        return ""
    days = (due - as_of).days
    if days < 0:
        return f"due {due.isoformat()} ({plural(-days)} overdue)"
    if days == 0:
        return f"due {due.isoformat()} (today)"
    return f"due {due.isoformat()} (in {plural(days)})"


def _ago(days: int) -> str:
    if days == 0:
        return "today"
    return f"{plural(days)} ago" if days > 0 else f"{plural(-days)} after the as-of date"
