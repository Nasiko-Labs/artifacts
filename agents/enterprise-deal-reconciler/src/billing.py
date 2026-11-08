"""Pure: the billing configuration read from the payments account (or pasted), checked against the tool
output, and the subscription the contract is compared with.

Everything the model returns is kept only when its ids (customer, subscription, schedule, price) appear in
this turn's tool output, and an item only when its amount and quantity are written there too.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Any

import amounts
from terms import json_object

CANDIDATE_STATUSES = frozenset({"active", "trialing", "past_due", "incomplete", "not_started"})
_DAYS_PER_YEAR = Decimal(365)
_WEEKS_PER_YEAR = Decimal(52)


@dataclass(frozen=True)
class Item:
    price_id: str
    unit_amount_minor: int
    currency: str
    interval: str              # day | week | month | year
    interval_count: int
    quantity: int
    nickname: str | None = None

    @property
    def months(self) -> Decimal:
        """How many months one billing interval lasts."""
        count = Decimal(self.interval_count)
        return {"month": count, "year": count * 12, "week": count * 12 / _WEEKS_PER_YEAR,
                "day": count * 12 / _DAYS_PER_YEAR}[self.interval]

    @property
    def unit_major(self) -> Decimal:
        return amounts.to_major(self.unit_amount_minor, self.currency)

    def unit_per(self, months: Decimal) -> Decimal:
        return amounts.per_period(self.unit_major, self.months, months)

    def total_per(self, months: Decimal) -> Decimal:
        return self.unit_per(months) * self.quantity

    def frequency(self) -> str | None:
        return {("month", 1): "monthly", ("month", 3): "quarterly", ("year", 1): "annual"}.get(
            (self.interval, self.interval_count))


@dataclass(frozen=True)
class Discount:
    percent_off: Decimal | None = None
    amount_off_minor: int | None = None
    coupon_id: str | None = None
    duration: str | None = None


@dataclass(frozen=True)
class Plan:
    """A subscription, or a not-yet-started schedule, the contract may correspond to."""

    id: str
    kind: str                      # subscription | schedule | pasted
    status: str
    items: tuple[Item, ...]
    collection_method: str | None = None
    days_until_due: int | None = None
    anchor: date | None = None
    start: date | None = None
    end: date | None = None
    discount: Discount | None = None
    link: str | None = None

    @property
    def first_invoice(self) -> date | None:
        return self.anchor or self.start

    def monthly_total(self) -> Decimal:
        return sum((i.total_per(Decimal(1)) for i in self.items), Decimal(0))


@dataclass(frozen=True)
class BillingConfig:
    source: str                    # tool | pasted
    customer_id: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None
    plans: tuple[Plan, ...] = ()
    livemode: bool | None = None
    dropped: int = 0


@dataclass(frozen=True)
class BillingAnswer:
    status: str
    config: BillingConfig | None = None
    candidates: tuple[Mapping[str, str | None], ...] = ()


# --- the model's answer ---------------------------------------------------------------------------

def parse_answer(text: str, seen_text: str) -> BillingAnswer | None:
    data = json_object(text)
    if not isinstance(data, Mapping) or not isinstance(data.get("status"), str):
        return None
    status = data["status"]
    if status == "multiple_customers":
        return BillingAnswer(status, candidates=_candidates(data.get("candidates"), seen_text))
    if status != "ok":
        return BillingAnswer(status)
    return BillingAnswer(status, _config(data, seen_text))


def _candidates(raw: Any, seen_text: str) -> tuple[Mapping[str, str | None], ...]:
    found = []
    for c in raw if isinstance(raw, list) else ():
        if isinstance(c, Mapping) and _seen(c.get("id"), seen_text):
            found.append({"id": str(c["id"]), "name": _text(c.get("name"), seen_text),
                          "email": _text(c.get("email"), seen_text)})
    return tuple({c["id"]: c for c in found}.values())[:10]


def _config(data: Mapping[str, Any], seen_text: str) -> BillingConfig:
    customer = data.get("customer") if isinstance(data.get("customer"), Mapping) else {}
    numbers = amounts.numbers_in(seen_text)
    schedules = [s for s in data.get("schedules") or () if isinstance(s, Mapping) and _seen(s.get("id"), seen_text)]
    plans: list[Plan] = []
    dropped = 0
    for raw in data.get("subscriptions") or ():
        plan, lost = _subscription(raw, seen_text, numbers, schedules)
        dropped += lost
        plans = [*plans, plan] if plan else plans
    attached = {p.id for p in plans}
    for raw in schedules:
        if raw.get("subscription_id") and str(raw.get("subscription_id")) in attached:
            continue
        plan, lost = _schedule(raw, seen_text, numbers)
        dropped += lost
        plans = [*plans, plan] if plan else plans
    livemode = data.get("livemode") if isinstance(data.get("livemode"), bool) and "livemode" in seen_text else None
    return BillingConfig("tool", customer_id=str(customer["id"]) if _seen(customer.get("id"), seen_text) else None,
                         customer_name=_text(customer.get("name"), seen_text),
                         customer_email=_text(customer.get("email"), seen_text),
                         plans=tuple(plans), livemode=livemode, dropped=dropped)


def _subscription(raw: Any, seen_text: str, numbers: frozenset, schedules: Sequence[Mapping]) -> tuple[Plan | None, int]:
    if not isinstance(raw, Mapping) or not _seen(raw.get("id"), seen_text):
        return None, 1
    items, lost = _items(raw.get("items"), seen_text, numbers)
    if not items:
        return None, 1 + lost
    sub_id = str(raw["id"])
    schedule = next((s for s in schedules if str(s.get("id")) == str(raw.get("schedule_id"))
                     or str(s.get("subscription_id")) == sub_id), None)
    end = amounts.utc_day(raw.get("cancel_at")) or (_last_phase_end(schedule) if schedule else None)
    return Plan(id=sub_id, kind="subscription", status=str(raw.get("status") or ""), items=items,
                collection_method=_word(raw.get("collection_method")),
                days_until_due=amounts.int_of(raw.get("days_until_due")),
                anchor=amounts.utc_day(raw.get("billing_cycle_anchor")), start=amounts.utc_day(raw.get("start_date")),
                end=end, discount=_discount(raw.get("discount"), seen_text, numbers),
                link=_text(raw.get("link"), seen_text)), lost


def _schedule(raw: Mapping[str, Any], seen_text: str, numbers: frozenset) -> tuple[Plan | None, int]:
    phases = [p for p in raw.get("phases") or () if isinstance(p, Mapping)]
    if not phases:
        return None, 1
    first = phases[0]
    items, lost = _items(first.get("items"), seen_text, numbers)
    if not items:
        return None, 1 + lost
    start = amounts.utc_day(first.get("start_date"))
    return Plan(id=str(raw["id"]), kind="schedule", status=str(raw.get("status") or "not_started"), items=items,
                anchor=start, start=start, end=_last_phase_end(raw),
                discount=_discount(first.get("discount"), seen_text, numbers)), lost


def _last_phase_end(schedule: Mapping[str, Any]) -> date | None:
    phases = [p for p in schedule.get("phases") or () if isinstance(p, Mapping)]
    return amounts.utc_day(phases[-1].get("end_date")) if phases else None


def _items(raw: Any, seen_text: str, numbers: frozenset) -> tuple[tuple[Item, ...], int]:
    kept: tuple[Item, ...] = ()
    lost = 0
    for r in raw if isinstance(raw, list) else ():
        item = _item(r, seen_text, numbers)
        kept, lost = ((*kept, item), lost) if item else (kept, lost + 1)
    return kept, lost


def _item(raw: Any, seen_text: str, numbers: frozenset) -> Item | None:
    if not isinstance(raw, Mapping) or not _seen(raw.get("price_id"), seen_text):
        return None
    unit = amounts.int_of(raw.get("unit_amount_minor"))
    quantity = amounts.int_of(raw.get("quantity"))
    interval = _word(raw.get("interval"))
    count = amounts.int_of(raw.get("interval_count")) or 1
    currency = _word(raw.get("currency"))
    if unit is None or unit < 0 or quantity is None or quantity < 0 or interval not in ("day", "week", "month", "year"):
        return None
    if Decimal(unit).normalize() not in numbers or Decimal(quantity).normalize() not in numbers or not currency:
        return None
    return Item(str(raw["price_id"]), unit, currency.upper(), interval, max(count, 1), quantity,
                _text(raw.get("nickname"), seen_text))


def _discount(raw: Any, seen_text: str, numbers: frozenset) -> Discount | None:
    if not isinstance(raw, Mapping):
        return None
    percent = amounts.decimal_of(raw.get("percent_off"))
    amount = amounts.int_of(raw.get("amount_off_minor"))
    percent = percent if percent is not None and percent > 0 and percent.normalize() in numbers else None
    amount = amount if amount is not None and amount > 0 and Decimal(amount).normalize() in numbers else None
    if percent is None and amount is None:
        return None
    coupon = raw.get("coupon_id")
    return Discount(percent, amount, str(coupon) if _seen(coupon, seen_text) else None, _word(raw.get("duration")))


def _seen(value: Any, seen_text: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value in seen_text


def _text(value: Any, seen_text: str) -> str | None:
    return str(value) if _seen(value, seen_text) else None


def _word(value: Any) -> str | None:
    return value.strip().casefold() if isinstance(value, str) and value.strip() else None


# --- target selection -----------------------------------------------------------------------------

def select(config: BillingConfig, contract_start: date | None) -> Plan | None:
    """The plan whose first billing date is nearest the contract start; ties → the highest total."""
    candidates = [p for p in config.plans if p.status in CANDIDATE_STATUSES or p.kind == "pasted"]
    if not candidates:
        return None

    def distance(p: Plan) -> int:
        if contract_start is None or p.first_invoice is None:
            return 10**6
        return abs((p.first_invoice - contract_start).days)

    return min(candidates, key=lambda p: (distance(p), -p.monthly_total(), p.id))


# --- pasted billing rows --------------------------------------------------------------------------

_KEYS = {
    "quantity": "quantity", "seats": "quantity", "unit_amount": "unit_amount", "unit_price": "unit_amount",
    "price": "unit_amount", "currency": "currency", "interval": "interval", "billing_interval": "interval",
    "interval_count": "interval_count", "anchor_date": "anchor", "billing_cycle_anchor": "anchor",
    "anchor": "anchor", "start_date": "start", "end_date": "end", "cancel_at": "end", "percent_off": "percent_off",
    "coupon_percent": "percent_off", "amount_off": "amount_off", "days_until_due": "days_until_due",
    "collection_method": "collection_method",
}
_KEY_LINE = re.compile(r"^\s*[-*]?\s*([A-Za-z_ ]{3,24}?)\s*[:=]\s*(.+?)\s*$")
_INTERVALS = {"month": ("month", 1), "monthly": ("month", 1), "quarter": ("month", 3), "quarterly": ("month", 3),
              "year": ("year", 1), "yearly": ("year", 1), "annual": ("year", 1), "annually": ("year", 1)}
MIN_KEYS = 2


def is_key_line(line: str) -> bool:
    match = _KEY_LINE.match(line)
    return bool(match) and match.group(1).strip().casefold().replace(" ", "_") in _KEYS


def billing_lines(text: str) -> dict[str, str]:
    """`key: value` lines (or one CSV header + row) naming billing fields, by canonical key."""
    found: dict[str, str] = {}
    lines = (text or "").splitlines()
    for line in lines:
        match = _KEY_LINE.match(line)
        if match:
            key = _KEYS.get(match.group(1).strip().casefold().replace(" ", "_"))
            if key and key not in found:
                found = {**found, key: match.group(2)}
    for header, row in itertools.pairwise(lines):
        names = [_KEYS.get(h.strip().casefold().replace(" ", "_")) for h in header.split(",")]
        values = row.split(",")
        if sum(1 for n in names if n) >= MIN_KEYS and len(values) == len(names):
            found = {**found, **{n: v.strip() for n, v in zip(names, values, strict=True) if n and v.strip()}}
    return found if len(found) >= MIN_KEYS else {}


def from_pasted(fields: Mapping[str, str]) -> BillingConfig | None:
    """One plan from pasted billing fields; None when there is no usable quantity and price."""
    currency = (fields.get("currency") or "").strip().upper() or None
    unit = amounts.decimal_of(_number(fields.get("unit_amount")))
    quantity = amounts.int_of(_number(fields.get("quantity")))
    if unit is None or quantity is None or not currency:
        return None
    interval, count = _INTERVALS.get((fields.get("interval") or "").strip().casefold(), (None, 1))
    if interval is None:
        return None
    count = amounts.int_of(fields.get("interval_count")) or count
    minor = int(amounts.half_up(unit.scaleb(amounts.minor_digits(currency)), 0))
    percent = amounts.decimal_of(_number(fields.get("percent_off")))
    amount = amounts.decimal_of(_number(fields.get("amount_off")))
    discount = None
    if (percent and percent > 0) or (amount and amount > 0):
        discount = Discount(percent if percent and percent > 0 else None,
                            int(amounts.half_up(amount.scaleb(amounts.minor_digits(currency)), 0))
                            if amount and amount > 0 else None)
    anchor = amounts.iso_date(fields.get("anchor") or "")
    start = amounts.iso_date(fields.get("start") or "") or anchor
    plan = Plan(id="pasted", kind="pasted", status="active",
                items=(Item("pasted", minor, currency, interval, count, quantity),),
                collection_method=_word(fields.get("collection_method")),
                days_until_due=amounts.int_of(_number(fields.get("days_until_due"))), anchor=anchor or start,
                start=start, end=amounts.iso_date(fields.get("end") or ""), discount=discount)
    return BillingConfig("pasted", plans=(plan,))


def _number(raw: str | None) -> str | None:
    if raw is None:
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", raw)
    return match.group(0) if match else None


def with_customer(config: BillingConfig, name: str | None) -> BillingConfig:
    return replace(config, customer_name=config.customer_name or name)
