"""User-facing text, rendered in code: the ranked list with its factor breakdown and bug links, the
coverage notes, the list the follow-up model reads, and copyable status replies. No raw HTML, no images;
links only from tool output or the pasted input."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from records import BugMatch, RiskFactor
from risk import Assessed

_PLAIN = ("tier", "priority", "age")
_UNSAFE = re.compile(r"[<>\[\]`*_|\\]")
_SPACES = re.compile(r"\s+")
_MAX_SUBJECT = 90


@dataclass(frozen=True)
class ListView:
    total: int
    window_days: int
    pasted: bool = False
    high: int = 0
    medium: int = 0
    notes: tuple[str, ...] = ()


def safe(text: str, most: int = _MAX_SUBJECT) -> str:
    """Untrusted text as one inert line: markdown and HTML characters removed, length capped."""
    flat = _SPACES.sub(" ", _UNSAFE.sub("", text or "")).strip()
    return flat if len(flat) <= most else flat[:most - 1].rstrip() + "…"


def headline(rows: Sequence[Assessed], view: ListView) -> str:
    source = "Pasted tickets" if view.pasted else f"Open tickets {view.total} (last {view.window_days} days)"
    if view.pasted:
        source += f" {view.total}"
    return f"{source}: {view.high} high risk, {view.medium} medium. Showing top {len(rows)}."


def factor_text(f: RiskFactor) -> str:
    label = f.evidence if f.name in _PLAIN else f"{f.name} {f.evidence}"
    return f"{label} {f.points}" if f.points else label


def link_ok(url: str) -> bool:
    return url.startswith(("https://", "http://")) and not re.search(r"[\s()<>\[\]`]", url)


def _ticket_ref(row: Assessed) -> str:
    return f"[#{row.ticket_id}]({row.url})" if link_ok(row.url) else f"#{row.ticket_id}"


def _bug_text(m: BugMatch) -> str:
    name = f"[{safe(m.issue_id, 40)}]({m.url})" if link_ok(m.url) else safe(m.issue_id, 40)
    state = safe(m.state, 30)
    if m.closed:
        return f"{name} (closed bug: check the fix shipped)"
    return f"{name} ({state})"


def row_line(index: int, row: Assessed, matches: Sequence[BugMatch]) -> str:
    factors = ", ".join(factor_text(f) for f in row.factors) or "no factor scored"
    line = (f"{index}. {_ticket_ref(row)} {safe(row.customer, 40)} - \"{safe(row.subject)}\" - "
            f"{row.risk_points} pts {row.risk_level.upper()} - {factors}")
    own = [m for m in matches if m.ticket_id == row.ticket_id]
    return line + (" - Bug: " + "; ".join(_bug_text(m) for m in own) if own else "")


def report(rows: Sequence[Assessed], matches: Sequence[BugMatch], view: ListView) -> str:
    parts = [headline(rows, view)]
    if rows:
        parts.append("\n".join(row_line(i, r, matches) for i, r in enumerate(rows, 1)))
        parts.append("Ask \"draft a status reply for #<ticket>\" for a copyable update. Points are a transparent "
                     "heuristic: overrule them when you know better.")
    else:
        parts.append("No ticket scored medium or high risk.")
    parts.extend(f"Note: {n}" for n in view.notes)
    return "\n\n".join(parts)


def as_json(env: dict) -> str:
    return "```json\n" + json.dumps(env, indent=1, ensure_ascii=False) + "\n```"


def for_followup(env: dict) -> str:
    """The stored result as plain lines for the follow-up model (it is fenced by the caller)."""
    lines = [str(env.get("summary") or "")]
    for i, row in enumerate(env.get("at_risk") or (), 1):
        factors = ", ".join(f"{f['name']} {f['evidence']} {f['points']}" for f in row.get("factors") or ())
        lines.append(f"{i}. #{row['ticket_id']} {row.get('customer')} - {row.get('subject')} - "
                     f"{row.get('risk_points')} pts {row.get('risk_level')} - {factors}")
    lines += [f"Bug for #{m['ticket_id']}: {m['issue_id']} {m['title']} ({m['state']})"
              for m in env.get("bug_matches") or ()]
    lines += [f"Note: {n}" for n in env.get("notes") or ()]
    return "\n".join(lines)


def copyable(ticket_id: str, customer: str, body: str) -> str:
    return (f"**Status reply for #{safe(ticket_id, 40)} ({safe(customer, 40)})**: copy it and send it yourself; "
            f"I have not sent anything.\n\n```text\n{body.replace('```', '')}\n```")
