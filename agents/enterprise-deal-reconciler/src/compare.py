"""Pure: the field-by-field comparison of contract terms with one billing plan.

Every tolerance and severity is a constant below. Not-stated contract terms go to `unverified` and never
count as a match or a mismatch. `fix` lines are text from code templates, never an action.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import amounts
from billing import BillingConfig, Item, Plan, select
from terms import LABELS, ContractTerms, Term

PRICE_TOLERANCE = Decimal("0.005")         # unit price: 0.5 %
NET_TOLERANCE = Decimal("0.01")            # net amount per period: above 1 % critical
NET_MEDIUM_FLOOR = Decimal("0.005")        # ... between 0.5 % and 1 % medium
TOTAL_FEE_TOLERANCE = Decimal("0.01")      # stated total vs seats x price: 1 %
DISCOUNT_TOLERANCE_PTS = Decimal("0.01")   # percentage points
SEAT_PRICE_MATCH = Decimal("0.01")         # an item is seat-priced when within 1 % of the contract price
DATE_TOLERANCE_DAYS = 1
FIRST_INVOICE_WINDOW_DAYS = 14
CLEAN_MIN_VERIFIED = 5
YEAR = Decimal(12)
MONTHS = {"month": Decimal(1), "quarter": Decimal(3), "year": Decimal(12)}
FREQUENCY_MONTHS = {"monthly": Decimal(1), "quarterly": Decimal(3), "annual": Decimal(12)}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}
# The output contract's field names (the catalog's, kept exactly); the only place they are spelled.
BILLING_VALUE_KEY = "stripe_value"
BILLING_ID_KEY = "stripe_id"


@dataclass(frozen=True)
class Mismatch:
    field: str
    kind: str              # mismatch | missing_discount | unexpected_discount | no_end_set | collection_method |
    #                        no_subscription | contract_internal_inconsistency | ambiguous_in_contract
    contract_value: str
    billing_value: str
    severity: str
    fix: str
    delta: str | None = None
    quote: str | None = None
    billing_id: str | None = None
    link: str | None = None

    def to_dict(self) -> dict:
        return {"field": self.field, "kind": self.kind, "contract_value": self.contract_value,
                BILLING_VALUE_KEY: self.billing_value, "delta": self.delta, "severity": self.severity,
                "quote": self.quote, BILLING_ID_KEY: self.billing_id, "link": self.link, "fix": self.fix}


@dataclass(frozen=True)
class Report:
    mismatches: tuple[Mismatch, ...]
    verified: tuple[str, ...]
    unverified: tuple[str, ...]
    verdict: str                     # clean | review | blocked
    plan: Plan | None
    first_invoice_days: int | None = None
    banner: str | None = None

    @property
    def compared(self) -> int:
        return len(set(self.verified) | {m.field for m in self.mismatches if m.kind != "ambiguous_in_contract"})

    def count(self, severity: str) -> int:
        return sum(1 for m in self.mismatches if m.severity == severity)


@dataclass(frozen=True)
class _Ctx:
    terms: ContractTerms
    plan: Plan
    seat_items: tuple[Item, ...]
    unit_months: Decimal | None          # the period the contract unit price is quoted for

    def term(self, field: str) -> Term | None:
        return self.terms.get(field)

    @property
    def main(self) -> Item:
        return self.seat_items[0]

    @property
    def currency(self) -> str | None:
        return self.terms.currency or self.main.currency

    def contract_net_unit(self) -> Decimal | None:
        price, seats, discount = self.term("unit_price"), self.term("seats"), self.term("discount")
        if price is None:
            return None
        if discount is None:
            return price.value
        if discount.value is not None:
            return price.value * (1 - discount.value / 100)
        if discount.amount is not None and seats is not None:
            return price.value - discount.amount / seats.value
        return None

    def billed_net_unit(self) -> Decimal | None:
        if self.unit_months is None:
            return None
        unit = self.main.unit_per(self.unit_months)
        off = self.plan.discount
        if off is None:
            return unit
        if off.percent_off is not None:
            return unit * (1 - off.percent_off / 100)
        quantity = sum(i.quantity for i in self.seat_items) or 1
        return unit - amounts.to_major(off.amount_off_minor or 0, self.main.currency) / quantity


Outcome = tuple[tuple[Mismatch, ...], tuple[str, ...], tuple[str, ...]]   # mismatches, verified, unverified


def reconcile(terms: ContractTerms, config: BillingConfig, today: date, customer: str | None) -> Report:
    ambiguous = tuple(_ambiguity(a) for a in terms.ambiguous)
    plan = select(config, _start(terms))
    if plan is None:
        who = customer or config.customer_name or "this customer"
        missing = Mismatch("subscription", "no_subscription", "contract terms", "none", "critical",
                           f"Create the subscription for {who} from the contract terms (a human step).",
                           billing_id=config.customer_id)
        return Report((missing, *ambiguous), (), _not_stated(terms), "blocked", None)
    ctx = _Ctx(terms, plan, _seat_items(terms, plan), _unit_months(terms))
    mismatches, verified, unverified = ambiguous, (), _not_stated(terms)
    for check in CHECKS:
        found, ok, unknown = check(ctx)
        mismatches, verified, unverified = (*mismatches, *found), (*verified, *ok), (*unverified, *unknown)
    ordered = tuple(sorted(mismatches, key=lambda m: SEVERITY_ORDER[m.severity]))
    return _finish(ordered, verified, unverified, plan, today)


def _finish(mismatches: Sequence[Mismatch], verified: Sequence[str], unverified: Sequence[str], plan: Plan,
            today: date) -> Report:
    critical = any(m.severity == "critical" for m in mismatches)
    verdict = "blocked" if critical else "review" if mismatches or len(verified) < CLEAN_MIN_VERIFIED else "clean"
    days = (plan.first_invoice - today).days if plan.first_invoice else None
    banner = (f"First invoice in {days} day{'s' if days != 1 else ''} ({plan.first_invoice.isoformat()}): fix the "
              "critical mismatches before it goes out." if critical and days is not None
              and 0 <= days <= FIRST_INVOICE_WINDOW_DAYS else None)
    return Report(tuple(mismatches), tuple(dict.fromkeys(verified)), tuple(dict.fromkeys(unverified)), verdict, plan,
                  days, banner)


def _start(terms: ContractTerms) -> date | None:
    term = terms.get("start_date")
    return term.value if term else None


def _unit_months(terms: ContractTerms) -> Decimal | None:
    price, frequency = terms.get("unit_price"), terms.get("billing_frequency")
    if price is not None and price.period in MONTHS:
        return MONTHS[price.period]
    if frequency is not None and frequency.value in FREQUENCY_MONTHS:
        return FREQUENCY_MONTHS[frequency.value]
    return None


def _seat_items(terms: ContractTerms, plan: Plan) -> tuple[Item, ...]:
    price, months = terms.get("unit_price"), _unit_months(terms)
    if price is not None and months is not None:
        close = tuple(i for i in plan.items if _rel(i.unit_per(months), price.value) <= SEAT_PRICE_MATCH)
        if close:
            return close
    return (max(plan.items, key=lambda i: (i.total_per(Decimal(1)), i.price_id)),)


def _rel(actual: Decimal, expected: Decimal) -> Decimal:
    return abs(actual - expected) / expected if expected else Decimal(0) if actual == expected else Decimal(1)


def _not_stated(terms: ContractTerms) -> tuple[str, ...]:
    return tuple(f"contract does not state {_article(LABELS[f])}" for f in terms.not_stated())


def _article(label: str) -> str:
    return f"an {label}" if label[0] in "aeiou" else f"a {label}"


def _ambiguity(a) -> Mismatch:
    shown = " / ".join(a.values) if a.values else "written ambiguously"
    return Mismatch(a.field, "ambiguous_in_contract", f"{a.reason}: {shown}", "not compared", "medium",
                    f"Confirm the {LABELS.get(a.field, a.field)} with the contract owner.",
                    quote=" | ".join(a.quotes))


def _mismatch(ctx: _Ctx, field: str, contract: str, billing: str, severity: str, fix: str, *,
              kind: str = "mismatch", delta: str | None = None, quote_of: str | None = None) -> Mismatch:
    term = ctx.term(quote_of or field)
    return Mismatch(field, kind, contract, billing, severity, fix, delta, term.quote if term else None,
                    ctx.plan.id if ctx.plan.kind != "pasted" else None, ctx.plan.link)


_NONE: Outcome = ((), (), ())


def _ok(field: str) -> Outcome:
    return (), (field,), ()


def _unknown(note: str) -> Outcome:
    return (), (), (note,)


def _one(m: Mismatch) -> Outcome:
    return (m,), (), ()


# --- the checks -----------------------------------------------------------------------------------

def check_seats(ctx: _Ctx) -> Outcome:
    seats = ctx.term("seats")
    if seats is None:
        return _NONE
    billed = sum(i.quantity for i in ctx.seat_items)
    if billed == seats.value:
        return _ok("seats")
    net = ctx.contract_net_unit()
    delta = f"{abs(seats.value - billed)} seat{'s' if abs(seats.value - billed) != 1 else ''}"
    if net is not None and ctx.unit_months is not None:
        yearly = amounts.per_period(net, ctx.unit_months, YEAR) * (seats.value - billed)
        side = "under-billed" if yearly > 0 else "over-billed"
        delta = f"{side} {amounts.money(abs(yearly), ctx.currency)}/yr"
    return _one(_mismatch(ctx, "seats", str(seats.value), f"{billed}", "critical",
                          f"Set quantity to {seats.value}{_on(ctx)}.", delta=delta))


def check_currency(ctx: _Ctx) -> Outcome:
    stated = ctx.terms.currency
    if stated is None:
        return _unknown("contract does not state a currency")
    billed = ctx.main.currency
    if stated.upper() == billed.upper():
        return _ok("currency")
    return _one(_mismatch(ctx, "currency", stated, billed, "critical",
                          f"Bill in {stated}: use a {stated} price{_on(ctx)}.", quote_of="unit_price"))


def check_unit_price(ctx: _Ctx) -> Outcome:
    price = ctx.term("unit_price")
    if price is None:
        return _NONE
    if ctx.unit_months is None:
        return _unknown("contract unit price has no stated period, so it was not compared")
    if not _same_currency(ctx):
        return _NONE
    billed = ctx.main.unit_per(ctx.unit_months)
    period = _period_word(ctx.unit_months)
    if _rel(billed, price.value) <= PRICE_TOLERANCE:
        return _ok("unit_price")
    return _one(_mismatch(ctx, "unit_price", f"{amounts.money(price.value, ctx.currency)}/{period}",
                          f"{amounts.money(billed, ctx.main.currency)}/{period}", "high",
                          f"Use a price of {amounts.money(price.value, ctx.currency)} per {period}{_on(ctx)}.",
                          delta=_pct_delta(billed, price.value)))


def check_discount(ctx: _Ctx) -> Outcome:
    stated, off = ctx.term("discount"), ctx.plan.discount
    if stated is None and off is None:
        return _NONE
    if stated is None:
        return _one(_mismatch(ctx, "discount", "not in contract", _off_text(ctx), "high",
                              f"Remove the discount{_coupon(ctx)}{_on(ctx)}, or get the contract amended.",
                              kind="unexpected_discount"))
    if off is None:
        return _one(_mismatch(ctx, "discount", stated.shown(), "none", "high",
                              f"Add a {stated.shown()} discount{_on(ctx)}.", kind="missing_discount"))
    contract_pct, billed_pct = _contract_pct(ctx), _billed_pct(ctx)
    if contract_pct is None or billed_pct is None:
        return _unknown("discount kinds differ (percent vs amount) and could not be compared")
    if abs(contract_pct - billed_pct) <= DISCOUNT_TOLERANCE_PTS:
        return _ok("discount")
    return _one(_mismatch(ctx, "discount", stated.shown(), _off_text(ctx), "high",
                          f"Change the discount to {stated.shown()}{_on(ctx)}.",
                          delta=f"{_pts(billed_pct - contract_pct)} pts"))


def _contract_pct(ctx: _Ctx) -> Decimal | None:
    stated, price, seats = ctx.term("discount"), ctx.term("unit_price"), ctx.term("seats")
    if stated.value is not None:
        return stated.value
    if price is None or seats is None or stated.amount is None:
        return None
    return stated.amount / (price.value * seats.value) * 100


def _billed_pct(ctx: _Ctx) -> Decimal | None:
    off = ctx.plan.discount
    if off.percent_off is not None:
        return off.percent_off
    total = sum((i.unit_major * i.quantity for i in ctx.seat_items), Decimal(0))
    return amounts.to_major(off.amount_off_minor or 0, ctx.main.currency) / total * 100 if total else None


def check_net_amount(ctx: _Ctx) -> Outcome:
    months = _compare_months(ctx)
    contract = _contract_net_per(ctx, months) if months is not None else None
    if contract is None or not _same_currency(ctx):
        return _NONE
    billed = _billed_net_per(ctx, months)
    rel = _rel(billed, contract)
    if rel <= NET_MEDIUM_FLOOR:
        return _ok("net_amount_per_period")
    severity = "critical" if rel > NET_TOLERANCE else "medium"
    period = _period_word(months)
    side = "under" if billed < contract else "over"
    return _one(_mismatch(ctx, "net_amount_per_period", f"{amounts.money(contract, ctx.currency)}/{period}",
                          f"{amounts.money(billed, ctx.main.currency)}/{period}", severity,
                          "Recheck seats, price and discount until billing matches the contract.",
                          delta=f"{side}-billed {amounts.money(abs(contract - billed), ctx.currency)}/{period} "
                                f"({_pct_delta(billed, contract)})", quote_of="total_fee"))


def _compare_months(ctx: _Ctx) -> Decimal | None:
    frequency, term = ctx.term("billing_frequency"), ctx.term("term_months")
    if frequency is not None and frequency.value in FREQUENCY_MONTHS:
        return FREQUENCY_MONTHS[frequency.value]
    if frequency is not None and frequency.value == "upfront":
        return Decimal(term.value) if term else YEAR
    return ctx.unit_months


def _contract_net_per(ctx: _Ctx, months: Decimal) -> Decimal | None:
    fee, term = ctx.term("total_fee"), ctx.term("term_months")
    if fee is not None and fee.period in MONTHS:
        return amounts.per_period(fee.value, MONTHS[fee.period], months)
    if fee is not None and fee.period == "term" and term is not None:
        return amounts.per_period(fee.value, Decimal(term.value), months)
    seats, net = ctx.term("seats"), ctx.contract_net_unit()
    if seats is None or net is None or ctx.unit_months is None:
        return None
    return amounts.per_period(net, ctx.unit_months, months) * seats.value


def _billed_net_per(ctx: _Ctx, months: Decimal) -> Decimal:
    gross = sum((i.total_per(months) for i in ctx.plan.items), Decimal(0))
    off = ctx.plan.discount
    if off is None:
        return gross
    if off.percent_off is not None:
        return gross * (1 - off.percent_off / 100)
    return gross - amounts.to_major(off.amount_off_minor or 0, ctx.main.currency)


def check_start(ctx: _Ctx) -> Outcome:
    start = ctx.term("start_date")
    if start is None:
        return _NONE
    plan = ctx.plan
    billed = [d for d in (plan.start, plan.anchor) if d is not None]
    if not billed:
        return _unknown("billing shows no start or anchor date")
    if any(abs((d - start.value).days) <= DATE_TOLERANCE_DAYS for d in billed):
        return _ok("start_date")
    first = plan.first_invoice
    early = first is not None and (start.value - first).days > DATE_TOLERANCE_DAYS
    shown = first.isoformat() if first else billed[0].isoformat()
    return _one(_mismatch(ctx, "start_date", start.value.isoformat(), shown, "critical" if early else "high",
                          f"Set the billing start (anchor) to {start.value.isoformat()}{_on(ctx)}.",
                          delta=f"{abs((billed[0] - start.value).days)} days "
                                f"{'before' if early else 'after' if billed[0] > start.value else 'from'} "
                                "the contract start"))


def contract_end(terms: ContractTerms) -> date | None:
    end, start, months = terms.get("end_date"), terms.get("start_date"), terms.get("term_months")
    if end is not None:
        return end.value
    if start is not None and months is not None:
        return amounts.add_months(start.value, months.value)
    return None


def check_end(ctx: _Ctx) -> Outcome:
    end = contract_end(ctx.terms)
    renew = ctx.term("auto_renew")
    if end is None:
        return _unknown("contract does not state an end date or term") if ctx.term("end_date") is None and \
            ctx.term("term_months") is None and ctx.term("start_date") is not None else _NONE
    quote_of = "end_date" if ctx.term("end_date") else "term_months"
    billed = ctx.plan.end
    if billed is None:
        if renew is not None and renew.value:
            return _NONE
        return _one(_mismatch(ctx, "end_date", end.isoformat(), "no end set", "medium",
                              f"Set the subscription to end on {end.isoformat()}{_on(ctx)}.", kind="no_end_set",
                              quote_of=quote_of))
    if abs((billed - end).days) <= DATE_TOLERANCE_DAYS:
        return _ok("end_date")
    return _one(_mismatch(ctx, "end_date", end.isoformat(), billed.isoformat(), "medium",
                          f"Set the subscription to end on {end.isoformat()}{_on(ctx)}.",
                          delta=f"{abs((billed - end).days)} days", quote_of=quote_of))


def check_frequency(ctx: _Ctx) -> Outcome:
    stated = ctx.term("billing_frequency")
    if stated is None:
        return _NONE
    billed = ctx.main.frequency()
    shown = billed or f"every {ctx.main.interval_count} {ctx.main.interval}(s)"
    term = ctx.term("term_months")
    upfront_ok = stated.value == "upfront" and billed == "annual" and (term is None or term.value <= YEAR)
    if billed == stated.value or upfront_ok:
        return _ok("billing_frequency")
    return _one(_mismatch(ctx, "billing_frequency", stated.value, shown, "high",
                          f"Bill {stated.value}: use a {_interval_for(stated.value)} price{_on(ctx)}."))


def check_payment_terms(ctx: _Ctx) -> Outcome:
    stated = ctx.term("payment_terms_days")
    if stated is None:
        return _NONE
    plan = ctx.plan
    if plan.collection_method == "charge_automatically":
        return _one(_mismatch(ctx, "payment_terms", f"net {stated.value}", "charged automatically", "medium",
                              f"Switch collection to send invoice with {stated.value} days to pay{_on(ctx)}.",
                              kind="collection_method", quote_of="payment_terms_days"))
    if plan.days_until_due is None:
        return _unknown("billing does not show the days until an invoice is due")
    if plan.days_until_due == stated.value:
        return _ok("payment_terms")
    return _one(_mismatch(ctx, "payment_terms", f"net {stated.value}", f"net {plan.days_until_due}", "medium",
                          f"Set days until due to {stated.value}{_on(ctx)}.",
                          delta=f"{abs(stated.value - plan.days_until_due)} days", quote_of="payment_terms_days"))


def check_auto_renew(ctx: _Ctx) -> Outcome:
    renew = ctx.term("auto_renew")
    if renew is None:
        return _NONE
    ends = ctx.plan.end
    if renew.value and ends is not None:
        return _one(_mismatch(ctx, "auto_renew", "auto-renews", f"ends {ends.isoformat()}", "medium",
                              f"Remove the scheduled end{_on(ctx)} (the contract auto-renews)."))
    if renew.value or ends is not None:
        return _ok("auto_renew")
    return _NONE  # does not renew and no end set: reported once, by the end-date check


def check_total_fee(ctx: _Ctx) -> Outcome:
    fee, seats, term = ctx.term("total_fee"), ctx.term("seats"), ctx.term("term_months")
    net = ctx.contract_net_unit()
    if fee is None or seats is None or net is None or ctx.unit_months is None:
        return _NONE
    months = MONTHS.get(fee.period or "") or (Decimal(term.value) if fee.period == "term" and term else None)
    if months is None:
        return _NONE
    expected = amounts.per_period(net, ctx.unit_months, months) * seats.value
    if _rel(fee.value, expected) <= TOTAL_FEE_TOLERANCE:
        return _ok("total_fee")
    return _one(_mismatch(ctx, "total_fee", amounts.money(fee.value, ctx.currency),
                          f"seats x price = {amounts.money(expected, ctx.currency)}", "medium",
                          "Ask the contract owner which figure is right before billing either.",
                          kind="contract_internal_inconsistency", delta=_pct_delta(fee.value, expected)))


CHECKS: tuple[Callable[[_Ctx], Outcome], ...] = (
    check_seats, check_currency, check_unit_price, check_discount, check_net_amount, check_start, check_end,
    check_frequency, check_payment_terms, check_auto_renew, check_total_fee,
)


# --- wording --------------------------------------------------------------------------------------

def _same_currency(ctx: _Ctx) -> bool:
    stated = ctx.terms.currency
    return stated is None or stated.upper() == ctx.main.currency.upper()


def _on(ctx: _Ctx) -> str:
    return f" on {ctx.plan.id}" if ctx.plan.kind != "pasted" else ""


def _coupon(ctx: _Ctx) -> str:
    coupon = ctx.plan.discount.coupon_id if ctx.plan.discount else None
    return f" ({coupon})" if coupon else ""


def _off_text(ctx: _Ctx) -> str:
    off = ctx.plan.discount
    if off.percent_off is not None:
        return f"{_pts(off.percent_off)}%"
    return amounts.money(amounts.to_major(off.amount_off_minor or 0, ctx.main.currency), ctx.main.currency) + " off"


def _pts(value: Decimal) -> str:
    return format(amounts.half_up(value, 2).normalize(), "f")


def _pct_delta(actual: Decimal, expected: Decimal) -> str:
    if not expected:
        return "n/a"
    change = (actual - expected) / expected * 100
    return f"{'+' if change > 0 else ''}{_pts(change)}%"


def _period_word(months: Decimal) -> str:
    return {Decimal(1): "month", Decimal(3): "quarter", YEAR: "year"}.get(months, f"{_pts(months)} months")


def _interval_for(frequency: str) -> str:
    return {"monthly": "monthly", "quarterly": "every-3-months", "annual": "yearly", "upfront": "yearly"}[frequency]
