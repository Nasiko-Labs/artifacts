"""Pure: a ticket's customer tier, ticket priority, and grouping by customer.

Tier = the first configured tier word (best first) found in the verified tier hint (organization or
ticket tags/fields), else "unknown". Groups: tier rank, then more tickets first, then oldest ticket."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from config import Config
from dates import parse_day
from records import Ticket

UNKNOWN_TIER = "unknown"
UNKNOWN_ORG = "Unknown organization"
_FAR_FUTURE = date.max


@dataclass(frozen=True)
class CustomerGroup:
    customer: str
    tier: str
    tickets: tuple[Ticket, ...]


def tier_of(hint: str, config: Config) -> str:
    lowered = (hint or "").casefold()
    return next((t for t in config.tier_order if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", lowered)),
                UNKNOWN_TIER)


def tier_rank(tier: str, config: Config) -> int:
    order = config.tier_order
    return order.index(tier) if tier in order else len(order)


def customer_of(ticket: Ticket) -> str:
    return ticket.organization_name or UNKNOWN_ORG


def created(ticket: Ticket) -> date:
    return parse_day(ticket.created_at) or _FAR_FUTURE


def priority(ticket: Ticket, config: Config) -> tuple:
    """Linked first, then tier, then oldest: the order tickets are kept and offered in."""
    return (ticket.match != "linked", tier_rank(ticket.tier, config), created(ticket), ticket.id)


def group(tickets: Sequence[Ticket], config: Config) -> tuple[CustomerGroup, ...]:
    by_customer: dict[str, tuple[Ticket, ...]] = {}
    for ticket in tickets:
        name = customer_of(ticket)
        by_customer = {**by_customer, name: (*by_customer.get(name, ()), ticket)}
    groups = [CustomerGroup(name, _best_tier(items, config), tuple(sorted(items, key=created)))
              for name, items in by_customer.items()]
    return tuple(sorted(groups, key=lambda g: _group_key(g, config)))


def _best_tier(tickets: Sequence[Ticket], config: Config) -> str:
    return min((t.tier for t in tickets), key=lambda tier: tier_rank(tier, config), default=UNKNOWN_TIER)


def _group_key(g: CustomerGroup, config: Config) -> tuple:
    unknown_org = g.customer == UNKNOWN_ORG
    return (unknown_org, tier_rank(g.tier, config), -len(g.tickets), created(g.tickets[0]), g.customer)


def age_days(ticket: Ticket, today: date) -> int | None:
    day = created(ticket)
    return None if day == _FAR_FUTURE else (today - day).days
