"""Pure: money normalisation, high value, rank score, priority and draft tone, all computed in code.
Thresholds apply in each invoice's own currency; currencies are never converted. The model never sees
or produces a number here."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import classify
from checks import parse_time
from failures import Failure

HIGH_VALUE_MINOR = 50_000          # 500.00 per invoice
HIGH_MONTHLY_MINOR = 20_000        # 200.00 per month
HIGH_LIFETIME_MINOR = 500_000      # 5,000.00 paid so far
RECOVERABLE_WEIGHTS = {"high": 3, "medium": 2, "low": 1}
VALUE_WEIGHT = 5
URGENCY_WEIGHTS = {"urgent": 4, "soon": 2, "scheduled": 0}
HIGH_PRIORITY = 28
MEDIUM_PRIORITY = 18
FRIENDLY_TENURE = 3
ZERO_DECIMAL = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF", "PYG", "RWF"})
_INTERVALS = {"month": "month", "monthly": "month", "year": "year", "yearly": "year", "annual": "year",
              "annually": "year", "week": "week", "weekly": "week", "day": "day", "daily": "day"}


def monthly_minor(amount_minor: int | None, interval: str | None, interval_count: int = 1) -> int | None:
    """Per-month amount: month / count; year / 12 / count; week x 52 / 12 / count; day x 365 / 12 / count.
    Rounded half up to a whole minor unit."""
    if amount_minor is None or amount_minor <= 0 or not interval:
        return None
    count = Decimal(max(1, interval_count))
    amount = Decimal(amount_minor)
    per_interval = {"month": amount, "year": amount / 12, "week": amount * 52 / 12,
                    "day": amount * 365 / 12}.get(_INTERVALS.get(interval.strip().lower(), ""))
    if per_interval is None:
        return None
    return int((per_interval / count).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def is_high_value(f: Failure) -> bool:
    return (f.amount_minor >= HIGH_VALUE_MINOR or (f.monthly_minor or 0) >= HIGH_MONTHLY_MINOR
            or f.lifetime_paid_minor >= HIGH_LIFETIME_MINOR)


@dataclass(frozen=True)
class Scored:
    failure: Failure
    reason: classify.Reason
    urgency: classify.Urgency
    high_value: bool
    score: int
    priority: str
    tier: str


def score(f: Failure, now: datetime) -> Scored:
    reason = classify.classify(f.decline_code, f.failure_message)
    urgency = classify.urgency(f.next_attempt_at, f.attempt_count, now)
    high = is_high_value(f)
    total = (RECOVERABLE_WEIGHTS[reason.recoverable] * 10 + (VALUE_WEIGHT if high else 0)
             + URGENCY_WEIGHTS[urgency.level])
    label = "High" if total >= HIGH_PRIORITY else "Medium" if total >= MEDIUM_PRIORITY else "Low"
    return Scored(f, reason, urgency, high, total, label, tone(f, reason, urgency))


def tone(f: Failure, reason: classify.Reason, urgency: classify.Urgency) -> str:
    """`careful` (fraud class, or a dispute/refund on file) > `direct` (attempt 2+ or urgent) > `friendly`."""
    if reason.klass == "fraud_block" or f.disputes or f.refunds:
        return "careful"
    if (f.attempt_count or 1) >= 2 or urgency.level == "urgent":
        return "direct"
    return "friendly"


def rank(failures: Sequence[Failure], now: datetime) -> tuple[Failure, ...]:
    """Score desc, then amount desc, then failed_at asc; renumbered from 1 (keys stay)."""
    def order(f: Failure) -> tuple:
        failed = parse_time(f.failed_at)
        return (-score(f, now).score, -f.amount_minor, failed.timestamp() if failed else float("inf"))

    ranked = sorted(failures, key=order)
    return tuple(replace(f, number=str(n)) for n, f in enumerate(ranked, start=1))


def money(amount_minor: int, currency: str) -> str:
    """12000050, "usd" → "120,000.50 USD"; zero-decimal currencies have no fraction."""
    code = (currency or "").upper()
    if code in ZERO_DECIMAL:
        return f"{amount_minor:,} {code}"
    units = Decimal(amount_minor) / 100
    return f"{units:,.2f} {code}"


def totals(failures: Sequence[Failure]) -> dict[str, int]:
    """Sum of amounts per currency, in first-seen order."""
    out: dict[str, int] = {}
    for f in failures:
        out = {**out, f.currency: out.get(f.currency, 0) + f.amount_minor}
    return out
