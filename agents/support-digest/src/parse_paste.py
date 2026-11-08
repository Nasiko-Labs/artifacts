"""Pure: tickets pasted into the chat when no support desk is connected. One block per ticket, opened by an
`id:` line, then one field per line:

    id: 4812
    created: 2026-11-03 09:00
    first reply: none
    solved: 2026-11-04 10:00
    priority: urgent
    status: open
    customer: Acme
    tier: enterprise
    subject: Cannot log in after update

`first reply: none` (or `-`) means the ticket has had no reply yet. A ticket with no status is `solved`
when it has a solved time, else `open`. Unknown lines are ignored."""

from __future__ import annotations

import re
from dataclasses import replace

from records import Ticket

_ID = re.compile(r"^\s*(?:ticket\s*)?id\s*:\s*#?\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_FIELD = re.compile(r"^\s*(created|first[ _]reply|replied|solved|resolved|priority|status|customer|organization|"
                    r"org|tier|plan|subject|url)\s*:\s*(.*)$", re.IGNORECASE)
_NAMES = {"created": "created_at", "first reply": "first_reply_at", "first_reply": "first_reply_at",
          "replied": "first_reply_at", "solved": "solved_at", "resolved": "solved_at", "priority": "priority",
          "status": "status", "customer": "organization", "organization": "organization", "org": "organization",
          "tier": "tier_hint", "plan": "tier_hint", "subject": "subject", "url": "url"}
_NONE = ("none", "-", "no", "never", "n/a", "not yet")


def request_part(text: str) -> str:
    """The user's own words before the first pasted ticket block (its fields are data, not filters)."""
    first = _ID.search(text or "")
    return (text or "")[:first.start()] if first else (text or "")


def looks_pasted(text: str) -> bool:
    return bool(_ID.search(text or ""))


def parse_pasted(text: str) -> tuple[Ticket, ...]:
    tickets: list[Ticket] = []
    for line in (text or "").splitlines():
        opened = _ID.match(line)
        if opened:
            tickets.append(Ticket(id=opened.group(1).lstrip("#")))
        elif tickets:
            tickets[-1] = _apply(tickets[-1], line)
    return tuple(_finish(t) for t in tickets)


def _apply(ticket: Ticket, line: str) -> Ticket:
    field = _FIELD.match(line)
    if not field:
        return ticket
    name = _NAMES[re.sub(r"\s+", " ", field.group(1).casefold())]
    value = field.group(2).strip()
    if name == "first_reply_at" and value.casefold() in _NONE:
        return replace(ticket, no_reply=True)
    if name == "solved_at" and value.casefold() in _NONE:
        return ticket
    return replace(ticket, **{name: value})


def _finish(ticket: Ticket) -> Ticket:
    if ticket.status:
        return ticket
    return replace(ticket, status="solved" if ticket.solved_at else "open")
