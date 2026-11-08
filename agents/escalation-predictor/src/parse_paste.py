"""Pure: tickets pasted into the chat when no support desk is connected. One block per ticket, opened by an
`id:` line, with optional header fields, then the conversation, one message per line:

    id: 4812
    priority: urgent
    tier: enterprise
    customer: Acme
    subject: Exports fail again
    created: 2026-10-27
    customer 2026-11-03 09:10: Exports fail again. This is unacceptable.
    agent 2026-11-03 11:00: Thanks, we are looking into it.

`internal` lines are private agent notes. A line that is none of these continues the previous message."""

from __future__ import annotations

import re
from dataclasses import replace

from records import Message, Ticket

_ID = re.compile(r"^\s*(?:ticket\s*)?id\s*:\s*#?\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_FIELD = re.compile(r"^\s*(priority|tier|plan|customer|organization|org|subject|status|created|updated|"
                    r"reopened|reopens|url)\s*:\s*(.*)$", re.IGNORECASE)
_MESSAGE = re.compile(r"^\s*(customer|agent|internal|system)\s*[\(\[]?\s*"
                      r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\s*(?:Z|UTC)?)?\s*[\)\]]?\s*:\s*(.*)$",
                      re.IGNORECASE)
_FIELD_NAMES = {"tier": "tier_hint", "plan": "tier_hint", "customer": "organization", "organization": "organization",
                "org": "organization", "subject": "subject", "status": "status", "created": "created_at",
                "updated": "updated_at", "priority": "priority", "url": "url"}


def request_part(text: str) -> str:
    """The user's own words before the first pasted ticket block (its header lines are data, not filters)."""
    first = _ID.search(text or "")
    return (text or "")[:first.start()] if first else (text or "")


def looks_pasted(text: str) -> bool:
    return bool(_ID.search(text or ""))


def _is_header(line: str) -> bool:
    """A field line with no timestamp before its colon."""
    head = line.split(":", 1)[0]
    return not re.search(r"\d{4}-\d{2}-\d{2}", head)


def parse_pasted(text: str) -> tuple[Ticket, ...]:
    tickets: list[Ticket] = []
    for line in (text or "").splitlines():
        opened = _ID.match(line)
        if opened:
            tickets.append(Ticket(id=opened.group(1).lstrip("#")))
        elif tickets and line.strip():
            tickets[-1] = _apply(tickets[-1], line)
    return tuple(replace(t, comments_known=bool(t.comments)) for t in tickets)


def _apply(ticket: Ticket, line: str) -> Ticket:
    """Before the first message a `customer: Acme` line is the header field; after it, a message."""
    field = _FIELD.match(line)
    message = None if field and not ticket.comments and _is_header(line) else _MESSAGE.match(line)
    if message:
        role = message.group(1).casefold()
        posted = (message.group(2) or "").replace("UTC", "").strip()
        added = Message(author_role="agent" if role == "internal" else role, posted_at=posted,
                        public=role != "internal", text=message.group(3).strip())
        return replace(ticket, comments=(*ticket.comments, added))
    if field:
        name, value = field.group(1).casefold(), field.group(2).strip()
        if name in ("reopened", "reopens"):
            return replace(ticket, reopen_count=int(value) if value.isdigit() else ticket.reopen_count)
        return replace(ticket, **{_FIELD_NAMES[name]: value})
    if ticket.comments:  # a continuation line of the last message
        last = ticket.comments[-1]
        return replace(ticket, comments=(*ticket.comments[:-1], replace(last, text=f"{last.text} {line.strip()}")))
    return ticket
