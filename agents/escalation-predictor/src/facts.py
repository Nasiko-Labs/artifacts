"""Pure: per-ticket facts computed in code from timestamps and author roles, against the turn's fixed clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from records import Ticket

_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class TicketFacts:
    age_days: int | None            # created → now; None when the creation time is unknown
    hours_unanswered: int | None    # since the last customer message if no public agent reply followed; None = unknown
    customer_msgs: int              # public customer comments
    reopen_count: int
    comments_known: bool


def parse_time(value: str) -> datetime | None:
    text = (value or "").strip().replace(" ", "T", 1)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def facts_for(ticket: Ticket, now: datetime) -> TicketFacts:
    created = parse_time(ticket.created_at)
    age = max(0, (now - created).days) if created else None
    public = [m for m in ticket.comments if m.public]
    customer = [m for m in public if m.author_role == "customer"]
    return TicketFacts(age_days=age, hours_unanswered=_unanswered(ticket, now), customer_msgs=len(customer),
                       reopen_count=ticket.reopen_count or 0, comments_known=ticket.comments_known)


def _unanswered(ticket: Ticket, now: datetime) -> int | None:
    if not ticket.comments_known:
        return None
    public = [m for m in ticket.comments if m.public and m.author_role in ("customer", "agent")]
    last_customer = max((i for i, m in enumerate(public) if m.author_role == "customer"), default=None)
    if last_customer is None:
        return 0
    if any(m.author_role == "agent" for m in public[last_customer + 1:]):
        return 0
    posted = parse_time(public[last_customer].posted_at)
    if posted is None:
        return None
    return max(0, int((now - posted).total_seconds() // _SECONDS_PER_HOUR))
