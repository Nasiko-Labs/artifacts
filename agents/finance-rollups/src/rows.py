"""Pure: the balance-transaction and invoice records, reading the model's JSON answers, and the post-check that keeps
only records backed by this turn's tool output. All money is integer minor units until rendering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

import checks

# A row with no reporting category is mapped from its transaction type (plan: risks).
_TYPE_TO_CATEGORY = {
    "charge": "charge", "payment": "charge", "refund": "refund", "payment_refund": "refund",
    "adjustment": "dispute", "payout": "payout",
    "transfer": "transfer", "payout_cancel": "payout_reversal", "payout_failure": "payout_reversal",
}
_TYPE_FEE_WORD = "_fee"   # the processor's own fee types all end in "_fee"
_CATEGORY_ALIASES = {"payment": "charge", "network_cost": "fee"}


class BalanceRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    created_at: int
    reporting_category: str
    type: str
    amount_minor: int
    fee_minor: int
    net_minor: int
    currency: str
    source_id: str | None = None
    mapped_from_type: bool = False

    @property
    def consistent(self) -> bool:
        return self.net_minor == self.amount_minor - self.fee_minor


class InvoiceRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    number: str | None = None
    paid_at: int
    amount_paid_minor: int
    currency: str
    subscription_id: str | None = None
    credit_notes_minor: int | None = None


@dataclass(frozen=True)
class Answer:
    status: str                 # ok | no_read_tool | off_topic
    raw: tuple[dict, ...]
    has_more: bool = False


@dataclass(frozen=True)
class Checked:
    rows: tuple
    dropped: int = 0
    inconsistent: int = 0
    mapped: int = 0


def parse_answer(text: str, key: str) -> Answer | None:
    """The model's `{"status", "has_more", key: [...]}` answer, or None when it is not that shape."""
    data = checks.load_json_object(text)
    if data is None:
        return None
    status = str(data.get("status") or "ok")
    raw = data.get(key)
    if status not in ("ok", "no_read_tool", "off_topic") or not isinstance(raw, list):
        return None
    return Answer(status, tuple(r for r in raw if isinstance(r, Mapping)), bool(data.get("has_more")))


def check_balance(raw: tuple[dict, ...], seen_raw: str, start: int, end: int) -> Checked:
    """Rows whose id, amounts and time are in the tool output and whose time is inside [start, end), deduplicated.
    A row whose net is not amount - fee is kept but counted inconsistent."""
    seen = checks.normalise_text(seen_raw)
    kept: dict[str, BalanceRow] = {}
    for record in raw:
        row = _balance(record, seen_raw, seen, start, end)
        if row is not None and row.id not in kept:
            kept[row.id] = row
    rows = tuple(sorted(kept.values(), key=lambda r: (r.created_at, r.id)))
    return Checked(rows, len(raw) - len(rows), sum(1 for r in rows if not r.consistent),
                   sum(1 for r in rows if r.mapped_from_type))


def check_invoices(raw: tuple[dict, ...], seen_raw: str, start: int, end: int) -> Checked:
    seen = checks.normalise_text(seen_raw)
    kept: dict[str, InvoiceRow] = {}
    for record in raw:
        invoice = _invoice(record, seen_raw, seen, start, end)
        if invoice is not None and invoice.id not in kept:
            kept[invoice.id] = invoice
    rows = tuple(sorted(kept.values(), key=lambda r: (r.paid_at, r.id)))
    return Checked(rows, len(raw) - len(rows))


def _backed_amount(value: Any, raw: str) -> int | None:
    amount = checks.int_of(value)
    if amount is None or not checks.number_in(abs(amount), raw):
        return None
    return amount


def _backed_time(value: Any, raw: str, seen: str, start: int, end: int) -> int | None:
    parsed = checks.parse_time(value)
    if parsed is None or not checks.time_backed(value, parsed, raw, seen):
        return None
    seconds = int(parsed.timestamp())
    return seconds if start <= seconds < end else None


def category_of(reporting_category: str, row_type: str) -> tuple[str, bool]:
    """(category, whether it was mapped from the type)."""
    given = reporting_category.casefold()
    if given and given not in ("null", "none"):
        return _CATEGORY_ALIASES.get(given, given), False
    kind = row_type.casefold()
    if kind in _TYPE_TO_CATEGORY:
        return _TYPE_TO_CATEGORY[kind], True
    return ("fee" if kind.endswith(_TYPE_FEE_WORD) else kind or "other"), True


def _balance(r: Mapping[str, Any], raw: str, seen: str, start: int, end: int) -> BalanceRow | None:
    row_id = checks.text_of(r.get("id"))
    if not checks.appears_in(row_id, seen):
        return None
    created = _backed_time(r.get("created_at"), raw, seen, start, end)
    amount, fee, net = (_backed_amount(r.get(k), raw) for k in ("amount_minor", "fee_minor", "net_minor"))
    if created is None or amount is None or fee is None or net is None:
        return None
    row_type = checks.text_of(r.get("type"))
    category, mapped = category_of(checks.text_of(r.get("reporting_category")), row_type)
    source = checks.text_of(r.get("source_id"))
    return BalanceRow(id=row_id, created_at=created, reporting_category=category, type=row_type, amount_minor=amount,
                      fee_minor=fee, net_minor=net, currency=checks.text_of(r.get("currency")).upper() or "USD",
                      source_id=source if source and source != "null" and checks.appears_in(source, seen) else None,
                      mapped_from_type=mapped)


def _invoice(r: Mapping[str, Any], raw: str, seen: str, start: int, end: int) -> InvoiceRow | None:
    invoice_id = checks.text_of(r.get("id"))
    if not checks.appears_in(invoice_id, seen):
        return None
    paid = _backed_time(r.get("paid_at"), raw, seen, start, end)
    amount = _backed_amount(r.get("amount_paid_minor"), raw)
    if paid is None or amount is None:
        return None
    credit = r.get("credit_notes_minor")
    credit_minor = None if credit in (None, "", "null") else _backed_amount(credit, raw)
    sub = checks.text_of(r.get("subscription_id"))
    number = checks.text_of(r.get("number"))
    return InvoiceRow(id=invoice_id, number=number if number and checks.appears_in(number, seen) else None,
                      paid_at=paid, amount_paid_minor=amount,
                      currency=checks.text_of(r.get("currency")).upper() or "USD",
                      subscription_id=sub if sub and sub != "null" and checks.appears_in(sub, seen) else None,
                      credit_notes_minor=credit_minor)
