"""Pure: the markdown table, the fenced JSON and the CSV-like view, all rendered in code."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence

from caveats import counts
from model import Ticket
from rules import PRIORITIES

CELL_MAX = 220


def _cell(text: str | None) -> str:
    clean = " ".join(str(text or "").split()).replace("|", "\\|")
    return clean if len(clean) <= CELL_MAX else clean[:CELL_MAX - 1] + "…"


def _owner(t: Ticket) -> str:
    return t.suggested_owner + (f" (assigned: {t.assignee})" if t.assignee else "")


def _row(t: Ticket) -> str:
    reason = t.reason.split(": ", 1)[1] if ": " in t.reason else t.reason
    cells = [t.label, t.priority, t.category, t.escalation_risk, _owner(t), reason]
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def header(all_tickets: Sequence[Ticket]) -> str:
    c = counts(all_tickets)
    split = ", ".join(f"{c[p]} {p}" for p in PRIORITIES)
    return f"**Triage of {len(all_tickets)} tickets** ({split}; {c['high_risk']} high risk)"


def needs_person(all_tickets: Sequence[Ticket]) -> str:
    picked = []
    for t in all_tickets:
        if t.priority == "P1":
            picked.append(f"{t.label} ({t.category}, {t.tier})" if t.tier != "unknown" else f"{t.label} ({t.category})")
        elif t.has("legal_threat") or t.has("security_concern"):
            names = [n for n in ("legal_threat", "security_concern") if t.has(n)]
            picked.append(f"{t.label} ({', '.join(names)})")
    return f"**Needs a person now:** {', '.join(picked)}" if picked else ""


def render(all_tickets: Sequence[Ticket], shown: Sequence[Ticket], notes: Sequence[str], view_note: str = "",
           sort_note: str = "sorted by priority, then risk, then age") -> str:
    parts = [header(all_tickets)]
    if view_note:
        parts.append(f"_{view_note}_")
    if shown:
        table = ["| Ticket | Priority | Category | Risk | Suggested owner | Reason |",
                 "| --- | --- | --- | --- | --- | --- |", *(_row(t) for t in shown)]
        parts.append("\n".join(table))
    else:
        parts.append("No tickets match that view. Say \"show all\" to see the whole batch.")
    person = needs_person(all_tickets)
    if person:
        parts.append(person)
    footer = " ".join([*notes, f"({sort_note.capitalize()}.)"])
    parts.append(f"_{footer}_")
    return "\n\n".join(parts)


def render_json(envelope: dict) -> str:
    return "```json\n" + json.dumps(envelope, ensure_ascii=False, indent=2) + "\n```"


def render_csv(shown: Sequence[Ticket]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["id", "priority", "category", "escalation_risk", "suggested_owner", "assignee", "reason"])
    for t in shown:
        writer.writerow([t.id, t.priority, t.category, t.escalation_risk, t.suggested_owner, t.assignee or "",
                         t.reason])
    return "```csv\n" + buffer.getvalue() + "```"
