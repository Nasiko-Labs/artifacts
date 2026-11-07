"""Pure: the exact markdown block the approval card shows, the write guard binds and the document receives.
Built in code only: no model writes a character of it.

    ## Week of 2026-11-02
    - 2026-11-03 | Use Postgres for the ledger | decided by Mira | #payments | https://...
      "going with Postgres for the ledger"
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

from models import Decision

NEW_DOC_TITLE = "Decision Log"


def week_of(today: date) -> date:
    return today - timedelta(days=today.weekday())


def week_header(today: date) -> str:
    return f"## Week of {week_of(today).isoformat()}"


def weekly_doc_title(today: date) -> str:
    return f"{NEW_DOC_TITLE} - week of {week_of(today).isoformat()}"


def clean(text: str) -> str:
    """One line, no raw HTML, no column separator."""
    return " ".join((text or "").replace("<", "‹").replace(">", "›").replace("|", "/").split())


def entry(decision: Decision) -> str:
    what = ("(implied) " if decision.implied else "") + clean(decision.decision)
    who = f"decided by {clean(decision.decided_by) or 'unknown'}"
    if decision.proposer:
        who += f" (proposed by {clean(decision.proposer)})"
    parts = [decision.decided_at, what, who, f"#{clean(decision.channel) or 'unknown'}", decision.link or "no link"]
    if decision.supersedes_date:
        parts.append(f"supersedes the earlier decision of {decision.supersedes_date}")
    quote = clean(decision.quote).replace('"', "'")
    return f"- {' | '.join(parts)}\n  \"{quote}\""


def block(decisions: Sequence[Decision], today: date, note: str = "") -> str:
    lines = [week_header(today), *(entry(d) for d in decisions)]
    if note.strip():
        lines.append(f"Note: {clean(note)}")
    return "\n".join(lines)
