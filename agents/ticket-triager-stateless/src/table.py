"""Pure: a triage table from an earlier reply, read back and re-cut with no call. Only rows and lines that are
in that reply are shown; filters on fields the table does not carry (tier, open, age) need the batch itself.

Rows are already sorted by priority, then risk, then age, so a stable re-sort by risk then priority keeps
age order within each group: "sort by risk" is exact."""

from __future__ import annotations

import re
from dataclasses import dataclass

from followup import View
from rules import PRIORITIES

RISKS = ("high", "medium", "low")
_TITLE = re.compile(r"^\*\*Triage of (\d+) tickets\*\*.*$", re.MULTILINE)
_CELLS = re.compile(r"(?<!\\)\|")
_ID = re.compile(r"#?\b([A-Za-z]*-?\d[\w-]*)\b")
TABLE_HEAD = ["| Ticket | Priority | Category | Risk | Suggested owner | Reason |", "| --- | --- | --- | --- | --- | --- |"]


@dataclass(frozen=True)
class Row:
    line: str
    label: str
    priority: str
    category: str
    risk: str
    owner: str
    reason: str


@dataclass(frozen=True)
class Table:
    title: str
    rows: tuple[Row, ...]
    person: str = ""            # the "Needs a person now" line, about the whole batch


def read(text: str) -> Table | None:
    title = _TITLE.search(text or "")
    if title is None or "\n_Showing " in text:
        return None
    rows = tuple(r for r in (_row(line) for line in text.splitlines()) if r is not None)
    if len(rows) != int(title.group(1)):
        return None
    person = next((line for line in text.splitlines() if line.startswith("**Needs a person now:**")), "")
    return Table(title.group(0), rows, person)


def _row(line: str) -> Row | None:
    cells = [c.strip() for c in _CELLS.split(line.strip())]
    if len(cells) != 8 or cells[0] or cells[-1] or cells[2] not in PRIORITIES or cells[4] not in RISKS:
        return None
    return Row(line.strip(), *cells[1:7])


def serves(view: View) -> bool:
    return not view.tiers and not view.open_only and view.sort in ("priority", "risk")


def render_view(found: Table, view: View) -> str:
    rows = [r for r in found.rows if (not view.priorities or r.priority in view.priorities)
            and (not view.risks or r.risk in view.risks)]
    if view.sort == "risk":
        rows = sorted(rows, key=lambda r: (RISKS.index(r.risk), PRIORITIES.index(r.priority)))
    rows = rows[:view.top] if view.top else rows
    parts = [found.title]
    if view.note():
        parts.append(f"_{view.note()}_")
    if rows:
        parts.append("\n".join([*TABLE_HEAD, *(r.line for r in rows)]))
    else:
        parts.append("No tickets match that view. Say \"show all\" to see the whole batch.")
    if found.person:
        parts.append(found.person)
    order = "risk, then priority, then age" if view.sort == "risk" else "priority, then risk, then age"
    parts.append(f"_(From the triage earlier in our conversation; sorted by {order}.)_")
    return "\n\n".join(parts)


def why(found: Table, question: str) -> str | None:
    """The named tickets' rows as a short answer, or None when no ticket id in the question is in the table."""
    by_label = {r.label.lstrip("#").casefold(): r for r in found.rows}
    named = [by_label[m.casefold()] for m in _ID.findall(question) if m.casefold() in by_label]
    if not named:
        return None
    lines = [f"**{r.label}**: {r.priority}, {r.category}, risk {r.risk}, suggested owner {r.owner}. {r.reason}"
             for r in dict.fromkeys(named)]
    note = "_(From the triage earlier in our conversation; paste the batch again for the full rule breakdown.)_"
    return "\n\n".join([*lines, note])
