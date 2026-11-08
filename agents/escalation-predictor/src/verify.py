"""Pure: the verbatim checks. Every id, link, date, comment and quote the model returns must appear in this
turn's input or tool output (`seen`); otherwise the item (or the field) is dropped. Quotes and comment text
are compared whitespace- and case-normalised, with JSON escapes undone."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TypeVar

from config import OPEN_STATUSES
from records import Issue, Message, Ticket

_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\u2019", "'"),
            ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'))
_SPACES = re.compile(r"\s+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_COMMENT_PROBE_CHARS = 80
T = TypeVar("T")


def normalise(text: str) -> str:
    for old, new in _ESCAPES:
        text = text.replace(old, new)
    return _SPACES.sub(" ", text).strip().casefold()


def contains(haystack_norm: str, needle: str) -> bool:
    probe = normalise(needle)
    return bool(probe) and probe in haystack_norm


def has_token(seen: str, value: str) -> bool:
    """`value` occurs in `seen` not glued to other letters or digits (so id 12 is not found inside 4812)."""
    if not value:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", seen) is not None


def _first_each(items: Sequence[T], key: Callable[[T], str]) -> list[T]:
    """Each id once, its first occurrence kept."""
    seen: dict[str, T] = {}
    for item in items:
        seen.setdefault(key(item), item)
    return list(seen.values())


@dataclass(frozen=True)
class TicketCheck:
    kept: tuple[Ticket, ...]
    dropped: int = 0
    closed: int = 0


def check_tickets(tickets: Sequence[Ticket], seen: str, max_comments: int) -> TicketCheck:
    seen_norm = normalise(seen)
    kept: list[Ticket] = []
    dropped = closed = 0
    for ticket in _first_each(tickets, lambda t: t.id):
        if not has_token(seen, ticket.id):
            dropped += 1
            continue
        if ticket.status and ticket.status.casefold() not in OPEN_STATUSES:
            closed += 1
            continue
        kept.append(_clean(ticket, seen, seen_norm, max_comments))
    return TicketCheck(tuple(kept), dropped, closed)


def _clean(ticket: Ticket, seen: str, seen_norm: str, max_comments: int) -> Ticket:
    comments = tuple(_clean_message(m, seen) for m in ticket.comments if _comment_seen(m, seen_norm))
    comments = comments[-max_comments:]
    return replace(ticket, url=ticket.url if ticket.url and ticket.url in seen else "",
                   created_at=_seen_date(ticket.created_at, seen), updated_at=_seen_date(ticket.updated_at, seen),
                   tier_hint=ticket.tier_hint if contains(seen_norm, ticket.tier_hint) else "",
                   organization=ticket.organization if contains(seen_norm, ticket.organization) else "",
                   comments=comments, comments_known=ticket.comments_known and bool(comments))


def _comment_seen(message: Message, seen_norm: str) -> bool:
    return contains(seen_norm, message.text[:_COMMENT_PROBE_CHARS])


def _clean_message(message: Message, seen: str) -> Message:
    return replace(message, posted_at=_seen_date(message.posted_at, seen))


def _seen_date(value: str, seen: str) -> str:
    match = _DATE.search(value or "")
    return value if match and match.group(0) in seen else ""


def check_issues(issues: Sequence[Issue], seen: str) -> tuple[tuple[Issue, ...], int]:
    seen_norm = normalise(seen)
    kept, dropped = [], 0
    for issue in _first_each(issues, lambda i: i.id):
        if not has_token(seen, issue.id) or not contains(seen_norm, issue.title):
            dropped += 1
            continue
        kept.append(replace(issue, url=issue.url if issue.url and issue.url in seen else "",
                            state=issue.state if contains(seen_norm, issue.state) else "unknown"))
    return tuple(kept), dropped
