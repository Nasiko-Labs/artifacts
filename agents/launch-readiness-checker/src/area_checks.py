"""Pure: the docs and marketing rows (DOC-1..3, MKT-1..3)."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import TYPE_CHECKING

from config import DOC_FRESH_DAYS, DOC_TEXT_CHARS
from mentions import in_title, mentions
from records import Row
from rules import cite, row

if TYPE_CHECKING:
    from checks import Inputs

# Placeholder markers, counted exactly: whole-word TODO / TBD / XXX, "lorem" any case, "[ ]" and "?? ".
_MARKERS = (("TODO", re.compile(r"\bTODO\b")), ("TBD", re.compile(r"\bTBD\b")), ("XXX", re.compile(r"\bXXX\b")),
            ("lorem", re.compile(r"lorem", re.IGNORECASE)), ("[ ]", re.compile(re.escape("[ ]"))),
            ("??", re.compile(re.escape("?? "))))


def marker_counts(text: str) -> dict[str, int]:
    counts = {name: len(rx.findall(text)) for name, rx in _MARKERS}
    return {k: v for k, v in counts.items() if v}


def reference_day(inputs: Inputs):
    """min(today, launch date): the day freshness and overdue are measured against."""
    return min(inputs.today, inputs.launch_date) if inputs.launch_date else inputs.today


def docs(inputs: Inputs, terms: tuple[str, ...]) -> tuple[Row, ...]:
    doc = next((d for d in inputs.docs or () if in_title(d.title, terms)), None)
    if doc is None:
        return (row("DOC-1", "fail", f"no document title mentions \"{inputs.launch}\" ({len(inputs.docs or ())} read)"),
                row("DOC-2", "unknown", "no launch doc found"), row("DOC-3", "unknown", "no launch doc found"))
    shown = cite(doc.id, f"\"{doc.title}\"", doc.url)
    since = reference_day(inputs) - timedelta(days=DOC_FRESH_DAYS)
    if doc.modified_at is None:
        fresh = row("DOC-2", "unknown", "last modified time not readable", [shown])
    elif doc.modified_at < since:
        fresh = row("DOC-2", "warn", f"last modified {doc.modified_at.isoformat()}, more than {DOC_FRESH_DAYS} days "
                                     f"before {reference_day(inputs).isoformat()}", [shown])
    else:
        fresh = row("DOC-2", "pass", f"last modified {doc.modified_at.isoformat()}", [shown])
    return (row("DOC-1", "pass", f"\"{doc.title}\"", [shown]), fresh, _finished(doc, shown))


def _finished(doc, shown: str) -> Row:
    if not doc.text.strip():
        return row("DOC-3", "unknown", f"text of \"{doc.title}\" not readable", [shown])
    counts = marker_counts(doc.text)
    scanned = f" (first {DOC_TEXT_CHARS:,} characters scanned)" if doc.capped else ""
    if not counts:
        return row("DOC-3", "pass", f"no placeholder markers{scanned}", [shown])
    total = sum(counts.values())
    parts = ", ".join(f"{n} {k}" for k, n in counts.items())
    label = next(iter(counts)) if len(counts) == 1 else "placeholder"
    return row("DOC-3", "warn", f"{total} {label} marker{'s' * (total != 1)} in \"{doc.title}\" ({parts}){scanned}",
               [shown])


def _in_scope(task, inputs: Inputs, terms: tuple[str, ...]) -> bool:
    hint = (inputs.marketing_project or "").casefold().strip()
    return (task.source == "pasted" or mentions(task.title, None, terms) or in_title(task.project, terms)
            or bool(hint and (task.project or "").casefold().strip() == hint))


def marketing(inputs: Inputs, terms: tuple[str, ...]) -> tuple[Row, ...]:
    tasks = [t for t in inputs.tasks or () if _in_scope(t, inputs, terms)]
    pasted = " (pasted)" if any(t.source == "pasted" for t in tasks) else ""
    if not tasks:
        return (row("MKT-1", "warn", f"no task mentions \"{inputs.launch}\" or is in its project"),
                row("MKT-2", "pass", "no launch tasks to be overdue"), row("MKT-3", "pass", "no launch tasks"))
    day = reference_day(inputs)
    open_ = [t for t in tasks if not t.completed]
    overdue = [t for t in open_ if t.due_date is not None and t.due_date < day]
    loose = [t for t in open_ if not t.assignee or t.due_date is None]
    n = len(tasks)
    return (
        row("MKT-1", "pass", f"{n} launch task{'s' * (n != 1)}{pasted}", [cite(t.id, t.title, t.url) for t in tasks]),
        row("MKT-2", "fail", f"{len(overdue)} open task{'s' * (len(overdue) != 1)} overdue (due before "
                             f"{day.isoformat()})", [f"{cite(t.id, t.title, t.url)} due {t.due_date.isoformat()}"
                                                     for t in overdue])
        if overdue else row("MKT-2", "pass", "nothing overdue"),
        row("MKT-3", "warn", f"{len(loose)} task{'s' * (len(loose) != 1)} without owner or due date",
            [cite(t.id, t.title, t.url) for t in loose]) if loose else row("MKT-3", "pass", "every open task has both"),
    )
