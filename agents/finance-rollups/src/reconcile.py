"""Pure: the ledger tie-out. Each mapped ledger total is compared with its rollup counterpart (revenue lines with
invoiced paid, refund lines with refunds, fee lines with fees) per month and currency. A match is within 1.00 or
0.5% of the rollup amount. A mismatch gets candidate explanations generated here from data only (timing,
refund_netting, fee_netting, credit_notes, currency_mismatch, missing_in_ledger, missing_in_payments), at most three,
the closest first; none fits -> unexplained. Unmapped ledger lines are listed, never forced into a pair."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

import months as m
import rollup
from ledger import FEES, REFUNDS, REVENUE, LedgerLine
from rollup import MonthRollup
from rows import InvoiceRow

MATCH_FLOOR_MINOR = 100            # 1.00
MATCH_PER_MILLE = 5                # 0.5%
BOUNDARY_DAYS = 2
MAX_CANDIDATES = 3
_DAY = int(timedelta(days=1).total_seconds())
_ITEM_FIELD = {REVENUE: "invoiced_paid", REFUNDS: "refunds", FEES: "fees"}
_ORDER = {REVENUE: 0, REFUNDS: 1, FEES: 2}
_ITEM_LABEL = {REVENUE: "revenue", REFUNDS: "refunds", FEES: "fees"}
MATCH, MISMATCH, UNMAPPED = "match", "mismatch", "unmapped"
UNEXPLAINED = "unexplained"


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    amount_minor: int | None = None
    detail: str = ""
    invoice_ids: tuple[str, ...] = ()


class Reconciliation(BaseModel):
    model_config = ConfigDict(frozen=True)

    month: str
    item: str
    currency: str | None = None
    ledger_amount: int | None = None
    payments_amount: int | None = None
    variance: int | None = None
    variance_pct: str | None = None
    status: str
    candidate_explanations: tuple[Candidate, ...] = ()

    @property
    def unexplained(self) -> bool:
        return self.status == MISMATCH and any(c.kind == UNEXPLAINED for c in self.candidate_explanations)


def within(difference: int, base: int | None) -> bool:
    """|difference| <= max(1.00, 0.5% of |base|), in integer arithmetic."""
    gap = abs(difference)
    return gap <= MATCH_FLOOR_MINOR or gap * 1000 <= MATCH_PER_MILLE * abs(base or 0)


def reconcile(lines: Sequence[LedgerLine], rollups: Sequence[MonthRollup], invoices: Sequence[InvoiceRow],
              month_keys: Sequence[str]) -> tuple[Reconciliation, ...]:
    """Pairs in month, item order, then missing-in-ledger months, then unmapped lines."""
    groups: dict[tuple[str, str, str | None], int] = {}
    unmapped: list[Reconciliation] = []
    for line in lines:
        currency = line.currency or _default_currency(line.month, rollups)
        if line.item is None or line.month not in month_keys:
            label = line.account if line.month in month_keys else f"{line.account} (outside the rolled-up months)"
            unmapped.append(Reconciliation(month=line.month, item=label, currency=currency,
                                           ledger_amount=line.amount_minor, status=UNMAPPED))
            continue
        amount = abs(line.amount_minor) if line.item in (REFUNDS, FEES) else line.amount_minor
        key = (line.month, line.item, currency)
        groups[key] = groups.get(key, 0) + amount
    pairs = [_pair(month, item, currency, amount, rollups, invoices)
             for (month, item, currency), amount in sorted(groups.items(), key=lambda kv: (kv[0][0], _ORDER[kv[0][1]]))]
    return (*pairs, *_missing_in_ledger(groups, rollups, month_keys), *unmapped)


def _default_currency(month: str, rollups: Sequence[MonthRollup]) -> str | None:
    same = [r for r in rollups if r.month == month] or list(rollups)
    return max(same, key=lambda r: r.gross).currency if same else None


def _find(rollups: Sequence[MonthRollup], month: str, currency: str | None) -> MonthRollup | None:
    return next((r for r in rollups if r.month == month and r.currency == currency), None)


def _pair(month: str, item: str, currency: str | None, ledger_amount: int, rollups: Sequence[MonthRollup],
          invoices: Sequence[InvoiceRow]) -> Reconciliation:
    label = _ITEM_LABEL[item]
    roll = _find(rollups, month, currency)
    if roll is None:
        kind = "currency_mismatch" if any(r.month == month for r in rollups) else "missing_in_payments"
        detail = (f"the ledger is in {currency}, the payments account has no {currency} activity that month"
                  if kind == "currency_mismatch" else "no payments activity that month")
        return Reconciliation(month=month, item=label, currency=currency, ledger_amount=ledger_amount, status=MISMATCH,
                              candidate_explanations=(Candidate(kind=kind, detail=detail),))
    there = getattr(roll, _ITEM_FIELD[item])
    if there is None:  # the invoiced view was not read this turn
        return Reconciliation(month=month, item=f"{label} (invoices not read)", currency=currency,
                              ledger_amount=ledger_amount, status=UNMAPPED)
    variance = ledger_amount - there
    base = Reconciliation(month=month, item=label, currency=currency, ledger_amount=ledger_amount,
                          payments_amount=there, variance=variance,
                          variance_pct=rollup.pct(variance, abs(there) or None),
                          status=MATCH)
    if within(variance, there):
        return base
    found = candidates(variance, there, roll, invoices) if item == REVENUE else ()
    return base.model_copy(update={"status": MISMATCH,
                                   "candidate_explanations": found or (Candidate(kind=UNEXPLAINED),)})


def candidates(variance: int, there: int, roll: MonthRollup, invoices: Sequence[InvoiceRow]) -> tuple[Candidate, ...]:
    """The data-backed explanations whose amount is within the match tolerance of the variance, closest first."""
    gap = abs(variance)
    options: list[Candidate] = list(_timing(variance, roll, invoices))
    for kind, amount, detail in (("refund_netting", roll.refunds, "the ledger may book revenue net of refunds"),
                                 ("fee_netting", roll.fees, "the ledger may book revenue net of fees"),
                                 ("credit_notes", roll.credit_notes or 0, "the month's credit notes")):
        if amount:
            options.append(Candidate(kind=kind, amount_minor=amount, detail=detail))
    fitting = [c for c in options if within(gap - (c.amount_minor or 0), there)]
    fitting.sort(key=lambda c: abs(gap - (c.amount_minor or 0)))
    return tuple(fitting[:MAX_CANDIDATES])


def _timing(variance: int, roll: MonthRollup, invoices: Sequence[InvoiceRow]) -> list[Candidate]:
    """Invoices paid within two days of a month boundary: for a ledger above the rollup, those paid in the last two
    days of the previous month or the first two days of the next; for a ledger below it, those paid in the first or
    last two days of the month itself."""
    start, end = m.bounds(roll.month)
    span = BOUNDARY_DAYS * _DAY
    if variance > 0:
        windows = (("paid in the last 2 days of the previous month", start - span, start),
                   ("paid in the first 2 days of the next month", end, end + span))
    else:
        windows = (("paid in the first 2 days of the month", start, start + span),
                   ("paid in the last 2 days of the month", end - span, end))
    out: list[Candidate] = []
    for detail, lo, hi in windows:
        found = [i for i in invoices if i.currency == roll.currency and lo <= i.paid_at < hi]
        if found:
            out.append(Candidate(kind="timing", amount_minor=sum(i.amount_paid_minor for i in found),
                                 detail=f"{len(found)} invoice(s) {detail}", invoice_ids=tuple(i.id for i in found)))
    return out


def _missing_in_ledger(groups: dict, rollups: Sequence[MonthRollup], month_keys: Sequence[str]) -> list[Reconciliation]:
    revenue_months = {month for month, item, _ in groups if item == REVENUE}
    if not revenue_months:
        return []
    out: list[Reconciliation] = []
    for month in month_keys:
        if month in revenue_months:
            continue
        for roll in (r for r in rollups if r.month == month and r.invoiced_paid):
            out.append(Reconciliation(month=month, item="revenue", currency=roll.currency,
                                      payments_amount=roll.invoiced_paid, status=MISMATCH,
                                      candidate_explanations=(Candidate(kind="missing_in_ledger",
                                                                        detail="no ledger revenue line that month"),)))
    return out
