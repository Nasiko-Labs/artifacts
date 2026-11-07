"""Pure: the risk score, band, grouping and order, all in code.

    signal_points  = sum(weights), counting only the larger of canceled / cancel_scheduled
    value_factor   = MRR in major units: < 100 → 1.0, < 1,000 → 1.25, < 5,000 → 1.5, else 2.0
    urgency_factor = 1.5 when a renewal or cancellation takes effect within 14 days, else 1.0
    risk_score     = round_half_up(signal_points * value_factor * urgency_factor, 1)
    band           = High >= 20, Medium >= 10, Low < 10

Accounts with no positive points, or only `renewal_near` / `recent_contact`, are not listed (renewals
among them are counted as healthy). Cancelled accounts form their own win-back group. Order: risk desc,
MRR desc, name asc; at most 25 listed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import mrr
import outreach
from signals import Account, Signal

MAX_LISTED = 25
URGENT_DAYS = 14
HIGH, MEDIUM = Decimal(20), Decimal(10)
_VALUE_STEPS = ((Decimal(100), Decimal("1.0")), (Decimal(1000), Decimal("1.25")), (Decimal(5000), Decimal("1.5")))
_TOP_VALUE = Decimal("2.0")
_HEALTHY_ONLY = frozenset({"renewal_near", "recent_contact"})
_EXCLUSIVE = ("canceled", "cancel_scheduled")


@dataclass(frozen=True)
class Row:
    id: str            # the number shown in the list: what "#2" refers to
    account: Account
    points: int
    risk_score: float
    band: str
    outreach: str
    angle: str
    group: str         # "at_risk" | "cancelled"

    @property
    def key(self) -> str:
        return self.account.id


@dataclass(frozen=True)
class Ranked:
    at_risk: tuple[Row, ...]
    cancelled: tuple[Row, ...]
    healthy_renewals: int = 0
    below_minimum: int = 0
    left_out: int = 0

    @property
    def rows(self) -> tuple[Row, ...]:
        return (*self.at_risk, *self.cancelled)


def signal_points(signals: Sequence[Signal]) -> int:
    exclusive = [s.weight for s in signals if s.kind in _EXCLUSIVE]
    rest = sum(s.weight for s in signals if s.kind not in _EXCLUSIVE)
    return rest + (max(exclusive) if exclusive else 0)


def value_factor(mrr_minor: int, currency: str) -> Decimal:
    major = mrr.to_major(mrr_minor, currency)
    return next((factor for limit, factor in _VALUE_STEPS if major < limit), _TOP_VALUE)


def urgency_factor(account: Account, today: date) -> Decimal:
    days = [(d - today).days for d in (date.fromisoformat(x) for x in account.effective_dates)]
    return Decimal("1.5") if any(0 <= n <= URGENT_DAYS for n in days) else Decimal("1.0")


def risk_score(account: Account, today: date) -> Decimal:
    raw = Decimal(signal_points(account.signals)) * value_factor(account.mrr_minor, account.currency)
    return (raw * urgency_factor(account, today)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def band(score: Decimal) -> str:
    return "High" if score >= HIGH else "Medium" if score >= MEDIUM else "Low"


def is_listed(account: Account) -> bool:
    return signal_points(account.signals) > 0 and not account.kinds <= _HEALTHY_ONLY


def rank(accounts: Sequence[Account], today: date, min_mrr: Decimal | None = None,
         focus: Sequence[str] = ()) -> Ranked:
    wanted = {f.casefold() for f in focus}
    pool = [a for a in accounts if not wanted or a.name.casefold() in wanted or a.id.casefold() in wanted]
    healthy = sum(1 for a in pool if not is_listed(a) and "renewal_near" in a.kinds)
    listed = [a for a in pool if is_listed(a)]
    kept = [a for a in listed if min_mrr is None or mrr.to_major(a.mrr_minor, a.currency) >= min_mrr]
    risky = sorted((_row(a, today, "at_risk") for a in kept if "canceled" not in a.kinds), key=_order)
    lost = sorted((_row(a, today, "cancelled") for a in kept if "canceled" in a.kinds), key=_order)
    shown = risky[:MAX_LISTED]
    numbered = [replace(r, id=str(n)) for n, r in enumerate([*shown, *lost], start=1)]
    return Ranked(tuple(numbered[:len(shown)]), tuple(numbered[len(shown):]), healthy, len(listed) - len(kept),
                  len(risky) - len(shown))


def _row(account: Account, today: date, group: str) -> Row:
    score = risk_score(account, today)
    kind = outreach.outreach_type(account.kinds)
    return Row(id="", account=account, points=signal_points(account.signals), risk_score=float(score),
               band=band(score), outreach=kind, angle=outreach.angle(account, kind), group=group)


def _order(row: Row) -> tuple:
    return (-row.risk_score, -mrr.to_major(row.account.mrr_minor, row.account.currency), row.account.name.casefold())


def mrr_at_risk(rows: Sequence[Row]) -> dict[str, int]:
    """High-band MRR per currency (never summed across currencies)."""
    totals: dict[str, int] = {}
    for r in rows:
        if r.band == "High":
            totals = {**totals, r.account.currency: totals.get(r.account.currency, 0) + r.account.mrr_minor}
    return totals


def top_quartile_floor(accounts: Sequence[Account]) -> Decimal | None:
    """The MRR (major units) at the 75th percentile of the scan, nearest-rank."""
    values = sorted(mrr.to_major(a.mrr_minor, a.currency) for a in accounts)
    if not values:
        return None
    index = max(0, -(-3 * len(values) // 4) - 1)
    return values[index]


def to_state(rows: Sequence[Row]) -> list[dict]:
    return [asdict(r) for r in rows]


def from_state(raw: Sequence[Mapping[str, Any]] | None) -> tuple[Row, ...]:
    return tuple(_row_from(item) for item in raw or ())


def _row_from(item: Mapping[str, Any]) -> Row:
    acc = dict(item.get("account") or {})
    names = {f.name for f in fields(Account)}
    account = Account(**{k: v for k, v in acc.items() if k in names and k not in ("signals", "not_checked",
                                                                                     "effective_dates")},
                      signals=tuple(Signal(**s) for s in acc.get("signals") or ()),
                      not_checked=tuple(acc.get("not_checked") or ()),
                      effective_dates=tuple(acc.get("effective_dates") or ()))
    return Row(id=str(item.get("id") or ""), account=account, points=int(item.get("points") or 0),
               risk_score=float(item.get("risk_score") or 0), band=str(item.get("band") or "Low"),
               outreach=str(item.get("outreach") or ""), angle=str(item.get("angle") or ""),
               group=str(item.get("group") or "at_risk"))
