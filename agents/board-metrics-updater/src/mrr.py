"""Pure: every metric is computed here, in integer minor units, never by the model.

Normalised monthly amount per item, discounts, which subscriptions pay at a time T (with plan changes
walked back from the event history), the per-customer start/end MRR, the bridge (new, expansion,
contraction, churned), and the ratios. Currencies are never combined."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from subs import Change, Item, Sub

NEVER_PAYING = frozenset({"incomplete", "incomplete_expired", "paused", "unpaid"})
_MONTHS_PER = {"month": Fraction(1), "year": Fraction(12), "week": Fraction(12, 52), "day": Fraction(12, 365)}
_REPEATING = frozenset({"forever", "repeating"})
_TENTH = Decimal("0.1")


def round_half_up(value: Fraction) -> int:
    return int((Decimal(value.numerator) / Decimal(value.denominator)).to_integral_value(rounding=ROUND_HALF_UP))


def item_monthly(item: Item, interval: str, interval_count: int) -> int | None:
    """Monthly amount of one item in minor units (half up), or None when it cannot be priced per month."""
    if not item.supported or interval not in _MONTHS_PER:
        return None
    base = Fraction(item.unit_amount_minor * item.quantity)
    return round_half_up(base / (_MONTHS_PER[interval] * max(1, interval_count)))


def sub_monthly(sub: Sub, items: Sequence[Item]) -> tuple[int, bool]:
    """(the subscription's MRR after discounts, whether an amount discount was ignored)."""
    total = sum(m for m in (item_monthly(i, sub.interval, sub.interval_count) for i in items) if m is not None)
    if sub.percent_off is not None:
        total = round_half_up(Fraction(total) * Fraction(100 - sub.percent_off) / 100)
    if sub.amount_off_minor is None:
        return total, False
    if sub.interval == "month" and sub.interval_count == 1 and sub.discount_duration in _REPEATING:
        return max(0, total - sub.amount_off_minor), False
    return total, True


def items_at(sub: Sub, changes: Iterable[Change], t: datetime) -> tuple[Item, ...]:
    """The items valid at `t`: start from today's and undo every change after `t`, newest first."""
    items = sub.items
    for change in sorted((c for c in changes if c.subscription_id == sub.id), key=lambda c: c.at, reverse=True):
        if change.at > t:
            items = change.previous_items
    return items


def state_at(sub: Sub, t: datetime) -> str:
    """'paying', 'trialing' or 'none' at time `t`."""
    if sub.status in NEVER_PAYING or sub.created_at > t:
        return "none"
    if sub.ends_at is not None and sub.ends_at <= t:
        return "none"
    if sub.trial_end is not None and sub.trial_end > t:
        return "trialing"
    return "paying"


@dataclass(frozen=True)
class Snapshot:
    by_customer: Mapping[str, int]
    trialing: int = 0
    past_due: int = 0
    amount_off_ignored: bool = False

    @property
    def total(self) -> int:
        return sum(self.by_customer.values())


def snapshot(subs: Sequence[Sub], changes: Sequence[Change], t: datetime, *, tag_past_due: bool = False) -> Snapshot:
    by_customer: dict[str, int] = {}
    trialing = past_due = 0
    ignored = False
    for sub in subs:
        state = state_at(sub, t)
        if state == "none":
            continue
        amount, skipped = sub_monthly(sub, items_at(sub, changes, t))
        ignored = ignored or skipped
        if state == "trialing":
            trialing += amount
            continue
        by_customer = {**by_customer, sub.customer_id: by_customer.get(sub.customer_id, 0) + amount}
        past_due += amount if tag_past_due and sub.status == "past_due" else 0
    return Snapshot(by_customer, trialing, past_due, ignored)


@dataclass(frozen=True)
class Block:
    """One currency's numbers for the period. Ratios are derived, never stored."""

    currency: str
    mrr0: int
    mrr1: int
    new_mrr: int
    reactivation_mrr: int
    expansion_mrr: int
    contraction_mrr: int
    churned_mrr: int
    customers0: int
    customers1: int
    churned_customers: int
    intra_period_churn_count: int = 0
    trialing_mrr: int = 0
    past_due_mrr: int = 0
    unsupported_items: int = 0
    total_items: int = 0
    amount_off_ignored: bool = False
    changes_known: bool = True

    @property
    def arr(self) -> int:
        return self.mrr1 * 12

    @property
    def net_new(self) -> int:
        return self.mrr1 - self.mrr0

    @property
    def reconcile_diff(self) -> int:
        """0 when start + new + expansion - contraction - churned == end."""
        bridge = self.mrr0 + self.new_mrr + self.expansion_mrr - self.contraction_mrr - self.churned_mrr
        return self.mrr1 - bridge

    def ratio(self, numerator: int, denominator: int) -> Decimal | None:
        if denominator == 0:
            return None
        return (Decimal(numerator) * 100 / Decimal(denominator)).quantize(_TENTH, rounding=ROUND_HALF_UP)

    @property
    def mom_pct(self) -> Decimal | None:
        return self.ratio(self.net_new, self.mrr0)

    @property
    def logo_churn_pct(self) -> Decimal | None:
        return self.ratio(self.churned_customers, self.customers0)

    @property
    def revenue_churn_pct(self) -> Decimal | None:
        return self.ratio(self.churned_mrr + self.contraction_mrr, self.mrr0)

    @property
    def nrr_pct(self) -> Decimal | None:
        return self.ratio(self.mrr0 + self.expansion_mrr - self.contraction_mrr - self.churned_mrr, self.mrr0)

    @property
    def grr_pct(self) -> Decimal | None:
        return self.ratio(self.mrr0 - self.contraction_mrr - self.churned_mrr, self.mrr0)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> Block:
        names = {f.name for f in fields(Block)}
        return Block(**{k: (v if isinstance(v, (bool, str)) else int(v)) for k, v in data.items() if k in names})


def compute(subs: Sequence[Sub], changes: Sequence[Change], t0: datetime, t1: datetime, *,
            changes_known: bool = True) -> tuple[Block, ...]:
    """One block per currency, in currency order."""
    currencies = sorted({s.currency for s in subs})
    return tuple(_block(c, [s for s in subs if s.currency == c], changes, t0, t1, changes_known) for c in currencies)


def _block(currency: str, subs: Sequence[Sub], changes: Sequence[Change], t0: datetime, t1: datetime,
           changes_known: bool) -> Block:
    start, end = snapshot(subs, changes, t0), snapshot(subs, changes, t1, tag_past_due=True)
    c0, c1 = start.by_customer, end.by_customer
    customers = set(c0) | set(c1)
    new = reactivation = expansion = contraction = churned = churned_customers = 0
    for customer in customers:
        before, after = c0.get(customer, 0), c1.get(customer, 0)
        if before == 0 and after > 0:
            returning = _had_ended_before(subs, customer, t0)
            reactivation += after if returning else 0
            new += after
        elif before > 0 and after == 0:
            churned += before
            churned_customers += 1
        elif before > 0 and after > before:
            expansion += after - before
        elif 0 < after < before:
            contraction += before - after
    considered = [s for s in subs if state_at(s, t0) != "none" or state_at(s, t1) != "none"]
    items = [i for s in considered for i in s.items]
    return Block(currency=currency, mrr0=start.total, mrr1=end.total, new_mrr=new, reactivation_mrr=reactivation,
                 expansion_mrr=expansion, contraction_mrr=contraction, churned_mrr=churned,
                 customers0=sum(1 for v in c0.values() if v > 0), customers1=sum(1 for v in c1.values() if v > 0),
                 churned_customers=churned_customers, intra_period_churn_count=_intra(subs, c0, c1, t0, t1),
                 trialing_mrr=end.trialing, past_due_mrr=end.past_due,
                 unsupported_items=sum(1 for i in items if not i.supported), total_items=len(items),
                 amount_off_ignored=start.amount_off_ignored or end.amount_off_ignored, changes_known=changes_known)


def _had_ended_before(subs: Sequence[Sub], customer: str, t0: datetime) -> bool:
    return any(s.customer_id == customer and s.ends_at is not None and s.ends_at <= t0 for s in subs)


def _intra(subs: Sequence[Sub], c0: Mapping[str, int], c1: Mapping[str, int], t0: datetime, t1: datetime) -> int:
    """Customers who joined and left inside the period: in neither new nor churned."""
    inside = {s.customer_id for s in subs if s.created_at > t0 and s.ends_at is not None and t0 < s.ends_at <= t1
              and s.status not in NEVER_PAYING}
    return len({c for c in inside if not c0.get(c) and not c1.get(c)})


def tone(block: Block) -> str:
    """'up', 'flat' (|MoM| < 0.5%) or 'down', from the code's own numbers."""
    pct = block.mom_pct
    if pct is None:
        return "up" if block.mrr1 > 0 else "flat"
    if pct >= Decimal("0.5"):
        return "up"
    return "down" if pct <= Decimal("-0.5") else "flat"
