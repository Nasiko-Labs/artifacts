"""Pure: normalised monthly amounts and per-customer MRR at a moment, in integer minor units, never by the model.

    base = unit_amount_minor * quantity; month / interval_count; year / (12 * interval_count);
    week * 52 / (12 * interval_count); day * 365 / (12 * interval_count); half-up integer rounding per item;
    percent_off applied to the subscription total (half up); tiered, metered and unpriced items excluded and
    counted.

A subscription counts at T when it is live (`created_at <= T < ended_at`, no end = live), was not in a trial at
T, and its status is one that pays: `active`, `past_due`, or `canceled` (it paid until it ended). `trialing`,
`paused`, `unpaid` and `incomplete*` never count. Items at T are today's items with every later plan change
undone, newest first."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from subs import Change, Item, Sub

PAYING_STATUSES = frozenset({"active", "past_due", "canceled"})
_MONTHS_PER = {"month": Fraction(1), "year": Fraction(12), "week": Fraction(12, 52), "day": Fraction(12, 365)}


def round_half_up(value: Fraction) -> int:
    return int((Decimal(value.numerator) / Decimal(value.denominator)).to_integral_value(rounding=ROUND_HALF_UP))


def item_monthly(item: Item, interval: str, interval_count: int) -> int | None:
    """Monthly amount of one item in minor units (half up), or None when it cannot be priced per month."""
    if not item.supported or interval not in _MONTHS_PER:
        return None
    base = Fraction(item.unit_amount_minor * item.quantity)
    return round_half_up(base / (_MONTHS_PER[interval] * max(1, interval_count)))


def sub_monthly(sub: Sub, items: Sequence[Item]) -> int:
    """The subscription's MRR for these items, after its percent discount."""
    total = sum(m for m in (item_monthly(i, sub.interval, sub.interval_count) for i in items) if m is not None)
    if sub.percent_off is None:
        return total
    return round_half_up(Fraction(total) * Fraction(100 - sub.percent_off) / 100)


def items_at(sub: Sub, changes: Iterable[Change], t: datetime) -> tuple[Item, ...]:
    """The items valid at `t`: start from today's and undo every change after `t`, newest first."""
    items = sub.items
    for change in sorted((c for c in changes if c.subscription_id == sub.id), key=lambda c: c.at, reverse=True):
        if change.at > t:
            items = change.previous_items
    return items


def counts_at(sub: Sub, t: datetime) -> bool:
    """Whether the subscription contributes MRR at `t`."""
    if sub.status not in PAYING_STATUSES or sub.created_at > t:
        return False
    if sub.ends_at is not None and sub.ends_at <= t:
        return False
    return not (sub.trial_end is not None and sub.trial_end > t)


def by_customer(subs: Sequence[Sub], changes: Sequence[Change], t: datetime) -> Mapping[str, int]:
    """Customer id -> total MRR at `t` (customers with nothing live are absent)."""
    out: dict[str, int] = {}
    for sub in subs:
        if counts_at(sub, t):
            out = {**out, sub.customer_id: out.get(sub.customer_id, 0) + sub_monthly(sub, items_at(sub, changes, t))}
    return out


def unsupported_items(subs: Sequence[Sub]) -> int:
    return sum(1 for s in subs for i in s.items if not i.supported)
