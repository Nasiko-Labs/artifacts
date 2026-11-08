"""Pure: every number the rules use, computed in code from the checked case: days since the charge, what is
still refundable, the matched plan and its period, how much of it was used, prior refunds and disputes,
tenure, a duplicate charge. The model never computes any of these."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from case import Charge, CustomerFile, Dispute, Msg, Subscription
from checks import parse_day, parse_time

DUPLICATE_WINDOW = timedelta(hours=24)
PRIOR_REFUND_WINDOW_DAYS = 365
OPEN_DISPUTE_STATUSES = frozenset({"needs_response", "under_review", "warning_needs_response", "warning_under_review",
                                   "open"})
_YEARLY = frozenset({"year", "annual", "yearly", "annually"})


@dataclass(frozen=True)
class Facts:
    charge_id: str | None = None
    charge_date: str | None = None
    days_since_charge: int | None = None
    amount_minor: int | None = None
    refundable_minor: int | None = None
    currency: str = ""
    paid: bool = False
    plan_name: str | None = None
    plan_interval: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    period_len_days: int | None = None
    days_used: int | None = None
    used_ratio: float | None = None
    unused_minor: int | None = None
    prior_refund_count_12m: int = 0
    prior_refund_total_minor: int = 0
    prior_disputes: int = 0
    customer_tenure_days: int | None = None
    duplicate_of: str | None = None        # the other charge of a duplicate pair
    duplicate_target: str | None = None    # the later charge of the pair: the one to refund
    duplicate_target_minor: int | None = None
    open_dispute_id: str | None = None     # an open dispute on this charge
    customer_quote: str | None = None      # a verified inbound quote stating the reason
    customer_quote_link: str | None = None
    window_end: str | None = None          # last day inside the refund window
    pasted: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> Facts:
        return Facts(**{k: v for k, v in dict(data or {}).items() if k in Facts.__dataclass_fields__})


def compute(charge: Charge | None, file: CustomerFile, recent: tuple[Charge, ...], dispute: Dispute | None,
            messages: tuple[Msg, ...], now: datetime, window_days: int) -> Facts:
    if charge is None:
        return Facts(**_history(file, None, now), **_quote(messages))
    created = parse_time(charge.created_at)
    charged_on = created.date() if created else None
    sub = match_subscription(charge, file.subscriptions)
    period = _period(charge, sub, charged_on, now.date())
    duplicate = _duplicate(charge, recent)
    return Facts(
        charge_id=charge.id, charge_date=charged_on.isoformat() if charged_on else None,
        days_since_charge=days_between(created, now) if created else None, amount_minor=charge.amount_minor,
        refundable_minor=max(0, charge.amount_minor - charge.refunded_minor), currency=charge.currency,
        paid=charge.paid and charge.status not in ("failed", "pending"),
        plan_name=sub.plan_name if sub else None, plan_interval=_interval(sub, file),
        **period, **_history(file, charge, now), **duplicate, open_dispute_id=_open_dispute(charge, file, dispute),
        **_quote(messages), window_end=(charged_on + timedelta(days=window_days)).isoformat() if charged_on else None,
        pasted=charge.source == "pasted")


def days_between(earlier: datetime, later: datetime) -> int:
    """floor(later - earlier) in whole days, never negative."""
    return max(0, math.floor((later - earlier).total_seconds() / 86400))


def match_subscription(charge: Charge, subs: tuple[Subscription, ...]) -> Subscription | None:
    """The subscription whose price equals the charge, else the only one."""
    priced = [s for s in subs if s.amount_minor == charge.amount_minor]
    if priced:
        return priced[0]
    return subs[0] if len(subs) == 1 else None


def _interval(sub: Subscription | None, file: CustomerFile) -> str | None:
    interval = (sub.interval if sub else None) or None
    return "year" if interval in _YEARLY else interval


def add_years(day: date, years: int = 1) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February in a non-leap year
        return day.replace(year=day.year + years, day=28)


def _period(charge: Charge, sub: Subscription | None, charged_on: date | None, today: date) -> dict:
    """The billing period the charge paid for: the subscription's current period when the charge falls in
    it, else one interval from the charge date. Only yearly plans get usage maths."""
    interval = "year" if sub and sub.interval in _YEARLY else (sub.interval if sub else None)
    if interval != "year" or charged_on is None:
        return {}
    start, end = parse_day(sub.current_period_start), parse_day(sub.current_period_end)
    if not (start and end and start <= charged_on < end):
        start, end = charged_on, add_years(charged_on)
    length = (end - start).days
    if length <= 0:
        return {}
    used = min(length, max(0, (today - start).days))
    ratio = Decimal(used) / Decimal(length)
    ratio = ratio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    unused = int((Decimal(charge.amount_minor) * (1 - ratio)).to_integral_value(rounding=ROUND_DOWN))
    refundable = max(0, charge.amount_minor - charge.refunded_minor)
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "period_len_days": length,
            "days_used": used, "used_ratio": float(ratio), "unused_minor": max(0, min(unused, refundable))}


def _history(file: CustomerFile, charge: Charge | None, now: datetime) -> dict:
    since = now - timedelta(days=PRIOR_REFUND_WINDOW_DAYS)
    recent = [r for r in file.prior_refunds if (t := parse_time(r.at)) is not None and since <= t <= now
              and (charge is None or r.charge_id != charge.id)]
    count = len(recent) if file.prior_refund_count is None else file.prior_refund_count
    paid = [d for d in (parse_time(i.paid_at) for i in file.invoices) if d is not None]
    return {"prior_refund_count_12m": count, "prior_refund_total_minor": sum(r.amount_minor or 0 for r in recent),
            "prior_disputes": len(file.prior_disputes),
            "customer_tenure_days": days_between(min(paid), now) if paid else None}


def _duplicate(charge: Charge, recent: tuple[Charge, ...]) -> dict:
    """Another paid charge with the same amount and description within 24 hours: refund the later one."""
    created = parse_time(charge.created_at)
    if created is None or not charge.paid:
        return {}
    for other in recent:
        when = parse_time(other.created_at)
        same = (other.id != charge.id and other.paid and other.amount_minor == charge.amount_minor
                and other.currency == charge.currency and (other.description or "") == (charge.description or ""))
        if when is not None and same and abs(when - created) <= DUPLICATE_WINDOW:
            later = other if when > created else charge
            return {"duplicate_of": other.id, "duplicate_target": later.id,
                    "duplicate_target_minor": max(0, later.amount_minor - later.refunded_minor)}
    return {}


def _open_dispute(charge: Charge, file: CustomerFile, dispute: Dispute | None) -> str | None:
    if dispute is not None and dispute.charge_id == charge.id and dispute.status in OPEN_DISPUTE_STATUSES:
        return dispute.id
    return next((d.id for d in file.prior_disputes if d.charge_id == charge.id and d.status in OPEN_DISPUTE_STATUSES),
                None)


def _quote(messages: tuple[Msg, ...]) -> dict:
    inbound = next((m for m in messages if m.direction == "in" and m.quote), None)
    return {"customer_quote": inbound.quote, "customer_quote_link": inbound.link} if inbound else {}
