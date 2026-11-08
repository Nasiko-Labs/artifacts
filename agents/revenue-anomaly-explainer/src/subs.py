"""Pure data: subscription, plan-change, failed-payment, customer and channel records, and reading the model's JSON
answers into them, each value checked against this turn's tool output (`seen`). A record whose id, amount or time
the tools never returned is dropped; an optional text (a cancellation reason, a decline code, a link) the tools
never returned is blanked, never kept. All money is integer minor units (cents) until rendering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import checks

UNSUPPORTED_SCHEMES = frozenset({"tiered", "volume", "graduated"})
INTERVALS = frozenset({"day", "week", "month", "year"})


@dataclass(frozen=True)
class Item:
    unit_amount_minor: int | None
    quantity: int = 1
    billing_scheme: str = "per_unit"
    usage_type: str = "licensed"

    @property
    def supported(self) -> bool:
        return (self.unit_amount_minor is not None and self.billing_scheme not in UNSUPPORTED_SCHEMES
                and self.usage_type != "metered")


@dataclass(frozen=True)
class Sub:
    id: str
    customer_id: str
    status: str
    currency: str
    interval: str
    interval_count: int
    items: tuple[Item, ...]
    created_at: datetime
    canceled_at: datetime | None = None
    ended_at: datetime | None = None
    trial_end: datetime | None = None
    percent_off: Decimal | None = None
    cancellation_reason: str | None = None
    link: str | None = None

    @property
    def ends_at(self) -> datetime | None:
        """When it stops counting: `ended_at`, or the cancel time of a canceled one with no end time."""
        if self.ended_at is not None:
            return self.ended_at
        return self.canceled_at if self.status == "canceled" else None


@dataclass(frozen=True)
class Change:
    subscription_id: str
    at: datetime
    previous_items: tuple[Item, ...]


@dataclass(frozen=True)
class Failure:
    id: str
    customer_id: str
    amount_minor: int
    currency: str
    at: datetime
    decline_code: str | None = None
    link: str | None = None


@dataclass(frozen=True)
class Customer:
    id: str
    name: str = ""
    email: str = ""

    @property
    def label(self) -> str:
        return self.name or self.email or self.id


@dataclass(frozen=True)
class Channel:
    id: str
    name: str

    @property
    def label(self) -> str:
        return "#" + self.name.lstrip("#")


@dataclass(frozen=True)
class SubsAnswer:
    status: str                      # ok | no_read_tool | off_topic
    raw: tuple[Mapping, ...]
    has_more: bool = False


# --- the model's answers ------------------------------------------------------------------------------------

def parse_subs_answer(text: str) -> SubsAnswer | None:
    data = checks.load_json_object(text)
    if data is None:
        return None
    status = str(data.get("status") or "ok")
    raw = data.get("subscriptions")
    if status not in ("ok", "no_read_tool", "off_topic") or not isinstance(raw, list):
        return None
    return SubsAnswer(status, tuple(r for r in raw if isinstance(r, Mapping)), bool(data.get("has_more")))


def list_of(text: str, key: str) -> tuple[Mapping, ...] | None:
    """The `key` list of the JSON object in a model reply, or None when there is no such object/list."""
    data = checks.load_json_object(text)
    raw = data.get(key) if data else None
    return tuple(r for r in raw if isinstance(r, Mapping)) if isinstance(raw, list) else None


def check_subs(raw: Sequence[Mapping], seen_raw: str) -> tuple[tuple[Sub, ...], int]:
    """(subscriptions whose ids, amounts and times are all in the tool output, how many were dropped)."""
    seen = checks.normalise_text(seen_raw)
    kept: tuple[Sub, ...] = ()
    for record in raw:
        sub = _sub(record, seen_raw, seen)
        kept = kept if sub is None or any(s.id == sub.id for s in kept) else (*kept, sub)
    return kept, len(raw) - len(kept)


def check_changes(raw: Sequence[Mapping], seen_raw: str, known: set[str]) -> tuple[Change, ...]:
    seen = checks.normalise_text(seen_raw)
    out: tuple[Change, ...] = ()
    for record in raw:
        sub_id = checks.text_of(record.get("subscription_id"))
        at = checks.parse_time(record.get("at"))
        items = _items(record.get("previous_items"), seen_raw)
        if sub_id in known and checks.time_backed(record.get("at"), at, seen_raw, seen) and items is not None:
            out = (*out, Change(sub_id, at, items))
    return out


def check_failures(raw: Sequence[Mapping], seen_raw: str) -> tuple[tuple[Failure, ...], int]:
    """(failed payments backed by tool output, deduplicated by payment id; how many were dropped)."""
    seen = checks.normalise_text(seen_raw)
    kept: tuple[Failure, ...] = ()
    for record in raw:
        failure = _failure(record, seen_raw, seen)
        if failure is not None and all(f.id != failure.id for f in kept):
            kept = (*kept, failure)
    return kept, len(raw) - len(kept)


def check_customers(raw: Sequence[Mapping], seen_raw: str, wanted: set[str]) -> dict[str, Customer]:
    seen = checks.normalise_text(seen_raw)
    out: dict[str, Customer] = {}
    for record in raw:
        cid = checks.text_of(record.get("id"))
        if cid not in wanted or not checks.appears_in(cid, seen):
            continue
        name, email = checks.text_of(record.get("name")), checks.text_of(record.get("email"))
        out = {**out, cid: Customer(cid, name if checks.appears_in(name, seen) else "",
                                    email if checks.appears_in(email, seen) else "")}
    return out


def check_channels(raw: Sequence[Mapping], seen_raw: str) -> tuple[Channel, ...]:
    seen = checks.normalise_text(seen_raw)
    out: tuple[Channel, ...] = ()
    for record in raw:
        cid, name = checks.text_of(record.get("id")), checks.text_of(record.get("name")).lstrip("#")
        if checks.appears_in(cid, seen) and checks.appears_in(name, seen) and all(c.id != cid for c in out):
            out = (*out, Channel(cid, name))
    return out


# --- one record -----------------------------------------------------------------------------------------------

def _optional_text(value: Any, seen: str) -> str | None:
    text = checks.text_of(value)
    return text if text and text.casefold() != "null" and checks.appears_in(text, seen) else None


def _optional_time(value: Any, raw: str, seen: str) -> tuple[bool, datetime | None]:
    """(acceptable, parsed): an absent value is fine; a present one must be backed by the tool output."""
    if value in (None, "", "null"):
        return True, None
    parsed = checks.parse_time(value)
    return checks.time_backed(value, parsed, raw, seen), parsed


def _sub(r: Mapping[str, Any], raw: str, seen: str) -> Sub | None:
    sub_id, customer = checks.text_of(r.get("id")), checks.text_of(r.get("customer_id"))
    created = checks.parse_time(r.get("created_at"))
    if not (checks.appears_in(sub_id, seen) and checks.appears_in(customer, seen)):
        return None
    if not checks.time_backed(r.get("created_at"), created, raw, seen):
        return None
    times = {}
    for key in ("canceled_at", "ended_at", "trial_end"):
        ok, parsed = _optional_time(r.get(key), raw, seen)
        if not ok:
            return None
        times[key] = parsed
    items = _items(r.get("items"), raw)
    interval = checks.text_of(r.get("interval")).casefold()
    if items is None or interval not in INTERVALS:
        return None
    return Sub(id=sub_id, customer_id=customer, status=checks.text_of(r.get("status")).casefold() or "active",
               currency=checks.text_of(r.get("currency")).upper() or "USD", interval=interval,
               interval_count=max(1, checks.int_of(r.get("interval_count")) or 1), items=items, created_at=created,
               canceled_at=times["canceled_at"], ended_at=times["ended_at"], trial_end=times["trial_end"],
               percent_off=_percent(r.get("percent_off"), raw),
               cancellation_reason=_optional_text(r.get("cancellation_reason"), seen),
               link=_optional_text(r.get("link"), seen))


def _items(raw_items: Any, raw: str) -> tuple[Item, ...] | None:
    if not isinstance(raw_items, list) or not raw_items:
        return None
    out: tuple[Item, ...] = ()
    for r in raw_items:
        if not isinstance(r, Mapping):
            return None
        amount = checks.int_of(r.get("unit_amount_minor"))
        if amount is not None and not checks.number_in(amount, raw):
            return None  # an amount the tools never returned
        quantity = checks.int_of(r.get("quantity"))
        out = (*out, Item(unit_amount_minor=amount, quantity=1 if quantity is None else max(0, quantity),
                          billing_scheme=checks.text_of(r.get("billing_scheme")).casefold() or "per_unit",
                          usage_type=checks.text_of(r.get("usage_type")).casefold() or "licensed"))
    return out


def _failure(r: Mapping[str, Any], raw: str, seen: str) -> Failure | None:
    fid, customer = checks.text_of(r.get("id")), checks.text_of(r.get("customer_id"))
    amount = checks.int_of(r.get("amount_minor"))
    at = checks.parse_time(r.get("at"))
    if not (checks.appears_in(fid, seen) and checks.appears_in(customer, seen)):
        return None
    if amount is None or not checks.number_in(amount, raw) or not checks.time_backed(r.get("at"), at, raw, seen):
        return None
    return Failure(id=fid, customer_id=customer, amount_minor=amount,
                   currency=checks.text_of(r.get("currency")).upper() or "USD", at=at,
                   decline_code=_optional_text(r.get("decline_code"), seen), link=_optional_text(r.get("link"), seen))


def _percent(value: Any, raw: str) -> Decimal | None:
    if value in (None, "", "null") or isinstance(value, bool):
        return None
    try:
        pct = Decimal(str(value))
    except InvalidOperation:
        return None
    text = format(pct.normalize(), "f")
    return pct if 0 < pct <= 100 and (text in raw or str(value) in raw) else None
