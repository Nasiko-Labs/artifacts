"""Pure: the verbatim checks. Every id, link, date, number, name and subject the model returns must appear in
this turn's tool output (`seen`); otherwise the item (or the field) is dropped. Text is compared
whitespace- and case-normalised, with JSON escapes undone."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from config import PRIORITIES, PRIORITY_ALIASES, norm_priority
from records import PreviousCount, SlaPolicy, Ticket

_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\u2019", "'"),
            ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'))
_SPACES = re.compile(r"\s+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def _seen_date(value: str, seen: str) -> str:
    match = _DATE.search(value or "")
    return value if match and match.group(0) in seen else ""


def _seen_number(value: int | None, seen: str) -> int | None:
    return value if value is not None and has_token(seen, str(value)) else None


@dataclass(frozen=True)
class TicketCheck:
    kept: tuple[Ticket, ...]
    dropped: int = 0


def check_tickets(tickets: Sequence[Ticket], seen: str) -> TicketCheck:
    """A ticket whose id or creation date is not in the tool output is dropped; other unverified fields are
    blanked (so their times become `unknown`, never assumed)."""
    seen_norm = normalise(seen)
    kept: dict[str, Ticket] = {}
    dropped = 0
    for ticket in tickets:
        if ticket.id in kept:
            continue
        if not has_token(seen, ticket.id) or not _seen_date(ticket.created_at, seen):
            dropped += 1
            continue
        kept[ticket.id] = _clean(ticket, seen, seen_norm)
    return TicketCheck(tuple(kept.values()), dropped)


def _clean(t: Ticket, seen: str, seen_norm: str) -> Ticket:
    def text(value: str) -> str:
        return value if contains(seen_norm, value) else ""

    return replace(t, url=t.url if t.url and t.url in seen else "", subject=text(t.subject), status=text(t.status),
                   priority=text(t.priority), tier_hint=text(t.tier_hint), organization=text(t.organization),
                   first_reply_at=_seen_date(t.first_reply_at, seen), solved_at=_seen_date(t.solved_at, seen),
                   reply_minutes=_seen_number(t.reply_minutes, seen),
                   resolve_minutes=_seen_number(t.resolve_minutes, seen))


def check_total(total: int | None, seen: str) -> int | None:
    """The total the search reported, only when that number is in the tool output."""
    return _seen_number(total, seen)


def check_previous(prev: PreviousCount, seen: str) -> PreviousCount:
    checked = check_tickets(prev.tickets, seen)
    return PreviousCount(count=_seen_number(prev.count, seen), tickets=checked.kept)


def check_policies(policies: Sequence[SlaPolicy], seen: str) -> tuple[SlaPolicy, ...]:
    """Policies whose priority maps onto a known one and whose minutes appear in the tool output."""
    seen_norm = normalise(seen)
    out: dict[str, SlaPolicy] = {}
    for p in policies:
        if not contains(seen_norm, p.priority):
            continue
        raw = p.priority.strip().casefold()
        name = norm_priority(raw)
        if (raw not in PRIORITIES and raw not in PRIORITY_ALIASES) or name in out:
            continue
        first, resolution = _seen_number(p.first_reply_minutes, seen), _seen_number(p.resolution_minutes, seen)
        if first is None and resolution is None:
            continue
        out[name] = SlaPolicy(priority=name, first_reply_minutes=first, resolution_minutes=resolution)
    return tuple(out.values())
