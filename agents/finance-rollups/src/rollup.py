"""Pure: the cash view (balance transactions by reporting category), the invoiced view (paid invoices by paid
month), the identity check, rates and month-over-month changes, per month and per currency, all in integer minor
units. Percentages are Decimal strings with one decimal (half-up), None when the base is zero."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict

import months as m
from rows import BalanceRow, InvoiceRow

ROW_CAP = 1_500
INVOICE_CAP = 600
_CHARGE, _REFUND, _FEE, _PAYOUT = "charge", "refund", "fee", "payout"
_DISPUTES = frozenset({"dispute", "dispute_reversal"})
_FEE_BEARING = frozenset({_CHARGE, _REFUND, _FEE, *_DISPUTES})
_NOT_OTHER = frozenset({*_FEE_BEARING, _PAYOUT})


class MonthRollup(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: str
    currency: str
    gross: int = 0
    refunds: int = 0
    disputes: int = 0
    fees: int = 0
    net: int = 0
    payouts: int = 0
    other: int = 0
    charges: int = 0
    refund_count: int = 0
    dispute_count: int = 0
    refund_rate: str | None = None
    fee_rate: str | None = None
    invoiced_paid: int | None = None
    subscription_share: str | None = None
    one_time_share: str | None = None
    credit_notes: int | None = None
    mom_gross_pct: str | None = None
    mom_net_pct: str | None = None
    mom_invoiced_pct: str | None = None
    partial: bool = False
    identity_ok: bool = True


@dataclass(frozen=True)
class Capped:
    rows: tuple
    remaining: int = 0
    partial_from: str | None = None   # the first month not fully covered


def pct(numerator: int, denominator: int | None) -> str | None:
    if not denominator:
        return None
    value = (Decimal(numerator) * 100 / Decimal(denominator)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return str(value)


def change_pct(current: int | None, previous: int | None) -> str | None:
    if current is None or previous is None or previous == 0:
        return None
    return pct(current - previous, abs(previous))


def cap(rows: Sequence, limit: int, time_of, has_more: bool = False) -> Capped:
    """The earliest `limit` rows; the month of the first row left out (or of the last row read, when pages were
    left unread) and every later month are partial."""
    ordered = tuple(sorted(rows, key=time_of))
    if len(ordered) > limit:
        return Capped(ordered[:limit], len(ordered) - limit, m.of_unix(time_of(ordered[limit])))
    if has_more and ordered:
        return Capped(ordered, 0, m.of_unix(time_of(ordered[-1])))
    return Capped(ordered)


def cash(rows: Iterable[BalanceRow]) -> dict:
    """The cash figures for one month and currency."""
    rows = tuple(rows)

    def amounts(categories) -> int:
        return sum(r.amount_minor for r in rows if r.reporting_category in categories)

    gross, refunds, disputes = amounts({_CHARGE}), -amounts({_REFUND}), -amounts(_DISPUTES)
    fees = sum(r.fee_minor for r in rows if r.reporting_category in _FEE_BEARING) - amounts({_FEE})
    net = sum(r.net_minor for r in rows if r.reporting_category != _PAYOUT)
    other = sum(r.net_minor for r in rows if r.reporting_category not in _NOT_OTHER)
    return {"gross": gross, "refunds": refunds, "disputes": disputes, "fees": fees, "net": net,
            "payouts": -amounts({_PAYOUT}), "other": other,
            "identity_ok": gross - refunds - disputes - fees + other == net,
            "charges": sum(1 for r in rows if r.reporting_category == _CHARGE),
            "refund_count": sum(1 for r in rows if r.reporting_category == _REFUND),
            "dispute_count": sum(1 for r in rows if r.reporting_category == "dispute"),
            "refund_rate": pct(refunds, gross), "fee_rate": pct(fees, gross)}


def invoiced(invoices: Iterable[InvoiceRow]) -> dict:
    invoices = tuple(invoices)
    total = sum(i.amount_paid_minor for i in invoices)
    subscription = sum(i.amount_paid_minor for i in invoices if i.subscription_id)
    return {"invoiced_paid": total, "subscription_share": pct(subscription, total),
            "one_time_share": pct(total - subscription, total),
            "credit_notes": sum(i.credit_notes_minor or 0 for i in invoices)}


def compute(rows: Sequence[BalanceRow], invoices: Sequence[InvoiceRow] | None, month_keys: Sequence[str],
            partial_from: str | None = None) -> tuple[MonthRollup, ...]:
    """One rollup per (currency, month in range), months in order; `invoices` None means the invoiced view was not
    read. Currencies are never mixed or converted."""
    currencies = sorted({r.currency for r in rows} | {i.currency for i in invoices or ()})
    out: list[MonthRollup] = []
    for currency in currencies:
        previous: MonthRollup | None = None
        for month in month_keys:
            figures = cash(r for r in rows if r.currency == currency and m.of_unix(r.created_at) == month)
            if invoices is not None:
                figures |= invoiced(i for i in invoices if i.currency == currency and m.of_unix(i.paid_at) == month)
            current = MonthRollup(month=month, currency=currency, partial=bool(partial_from and month >= partial_from),
                                  **figures)
            current = _with_mom(current, previous)
            out.append(current)
            previous = current
    return tuple(out)


def _with_mom(current: MonthRollup, previous: MonthRollup | None) -> MonthRollup:
    if previous is None:
        return current
    return current.model_copy(update={
        "mom_gross_pct": change_pct(current.gross, previous.gross),
        "mom_net_pct": change_pct(current.net, previous.net),
        "mom_invoiced_pct": change_pct(current.invoiced_paid, previous.invoiced_paid)})


def from_summary(rows: Sequence[dict], month_keys: Sequence[str] | None = None) -> tuple[MonthRollup, ...]:
    """Rollups from pasted monthly summary rows (already in minor units), with rates and MoM computed here."""
    out: list[MonthRollup] = []
    by_currency: dict[str, MonthRollup] = {}
    for row in sorted(rows, key=lambda r: (r["currency"], r["month"])):
        gross, refunds, fees = row.get("gross", 0), row.get("refunds", 0), row.get("fees", 0)
        current = MonthRollup(month=row["month"], currency=row["currency"], gross=gross, refunds=refunds,
                              disputes=row.get("disputes", 0), fees=fees, net=row.get("net", 0),
                              payouts=row.get("payouts", 0), invoiced_paid=row.get("invoiced_paid"),
                              refund_rate=pct(refunds, gross), fee_rate=pct(fees, gross))
        previous = by_currency.get(current.currency)
        if previous is not None and previous.month == m.shift(current.month, -1):
            current = _with_mom(current, previous)
        by_currency[current.currency] = current
        out.append(current)
    return tuple(out)


def latest(rollups: Sequence[MonthRollup]) -> tuple[MonthRollup, ...]:
    """The latest month's rollup per currency."""
    by_currency: dict[str, MonthRollup] = {}
    for r in rollups:
        if r.currency not in by_currency or r.month > by_currency[r.currency].month:
            by_currency[r.currency] = r
    return tuple(by_currency[c] for c in sorted(by_currency))
