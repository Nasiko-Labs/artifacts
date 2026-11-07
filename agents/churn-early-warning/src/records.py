"""Pure data: what the read steps return (subscriptions, changes, failed payments, mail, tickets),
reading the model's JSON for each, and small value helpers. Verification lives in checks.py."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_INT = re.compile(r"^-?\d+(?:\.0+)?$")


@dataclass(frozen=True)
class Item:
    unit_amount_minor: int | None
    quantity: int = 1
    billing_scheme: str = "per_unit"
    usage_type: str = "licensed"


@dataclass(frozen=True)
class Sub:
    id: str
    customer_id: str
    status: str
    currency: str
    interval: str = "month"
    interval_count: int = 1
    items: tuple[Item, ...] = ()
    customer_email: str = ""
    customer_name: str = ""
    percent_off: float | None = None
    current_period_end: str = ""
    cancel_at_period_end: bool = False
    cancel_at: str = ""
    canceled_at: str = ""
    trial_end: str = ""
    cancellation_reason: str = ""
    cancellation_comment: str = ""
    created_at: str = ""
    link: str = ""


@dataclass(frozen=True)
class Change:
    subscription_id: str
    at: str
    type: str                       # "updated" | "deleted"
    previous_items: tuple[Item, ...] = ()
    items: tuple[Item, ...] = ()


@dataclass(frozen=True)
class Failure:
    customer_id: str
    at: str
    invoice_id: str


@dataclass(frozen=True)
class Inbound:
    at: str
    subject: str
    link: str
    sender: str = ""
    thread_id: str = ""
    quote: str | None = None


@dataclass(frozen=True)
class MailRecord:
    account_id: str
    inbound: tuple[Inbound, ...] = ()
    last_inbound_at: str = ""
    last_sent_at: str = ""


@dataclass(frozen=True)
class Ticket:
    account_id: str
    id: str
    priority: str = ""
    status: str = ""
    subject: str = ""
    created_at: str = ""
    link: str = ""


def load_json_object(text: str) -> dict | None:
    """The first-to-last-brace JSON object in a model reply, code fences tolerated."""
    body = _CODE_FENCE.sub("", (text or "").strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def text_of(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def int_of(value: Any, default: int | None = None) -> int | None:
    text = text_of(value)
    return int(float(text)) if _INT.match(text) else default


def parse_time(value: Any) -> datetime | None:
    """An ISO date/datetime or epoch seconds, as an aware UTC datetime."""
    text = text_of(value)
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_day(value: Any) -> date | None:
    parsed = parse_time(value)
    return parsed.date() if parsed else None


def _list(data: Mapping[str, Any] | None, key: str) -> list | None:
    raw = data.get(key) if data else None
    return raw if isinstance(raw, list) else None


def _item(raw: Any) -> Item | None:
    if not isinstance(raw, Mapping):
        return None
    return Item(unit_amount_minor=int_of(raw.get("unit_amount_minor")),
                quantity=int_of(raw.get("quantity"), 1) or 0,
                billing_scheme=text_of(raw.get("billing_scheme")).lower() or "per_unit",
                usage_type=text_of(raw.get("usage_type")).lower() or "licensed")


def _items(raw: Any) -> tuple[Item, ...]:
    return tuple(i for i in (_item(x) for x in raw or ()) if i is not None) if isinstance(raw, list) else ()


def _sub(raw: Any) -> Sub | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")) or not text_of(raw.get("customer_id")):
        return None
    percent = raw.get("percent_off")
    return Sub(
        id=text_of(raw.get("id")), customer_id=text_of(raw.get("customer_id")),
        status=text_of(raw.get("status")).lower(), currency=text_of(raw.get("currency")).upper(),
        interval=text_of(raw.get("interval")).lower() or "month",
        interval_count=int_of(raw.get("interval_count"), 1) or 1, items=_items(raw.get("items")),
        customer_email=text_of(raw.get("customer_email")), customer_name=text_of(raw.get("customer_name")),
        percent_off=float(percent) if isinstance(percent, (int, float)) and not isinstance(percent, bool) else None,
        current_period_end=text_of(raw.get("current_period_end")),
        cancel_at_period_end=raw.get("cancel_at_period_end") is True, cancel_at=text_of(raw.get("cancel_at")),
        canceled_at=text_of(raw.get("canceled_at")), trial_end=text_of(raw.get("trial_end")),
        cancellation_reason=text_of(raw.get("cancellation_reason")),
        cancellation_comment=text_of(raw.get("cancellation_comment")),
        created_at=text_of(raw.get("created_at")), link=text_of(raw.get("link")),
    )


def parse_subscriptions(text: str) -> tuple[Sub, ...] | None:
    """None when the answer is not the agreed JSON; `off_topic` answers are handled by the caller."""
    raw = _list(load_json_object(text), "subscriptions")
    return None if raw is None else tuple(s for s in (_sub(x) for x in raw) if s is not None)


def is_off_topic(text: str) -> bool:
    data = load_json_object(text)
    return bool(data) and data.get("status") == "off_topic"


def parse_changes(text: str) -> tuple[tuple[Change, ...], tuple[Failure, ...]] | None:
    data = load_json_object(text)
    changes, failures = _list(data, "changes"), _list(data, "failures")
    if changes is None and failures is None:
        return None
    parsed = tuple(
        Change(subscription_id=text_of(c.get("subscription_id")), at=text_of(c.get("at")),
               type=text_of(c.get("type")).lower(), previous_items=_items(c.get("previous_items")),
               items=_items(c.get("items")))
        for c in changes or () if isinstance(c, Mapping))
    fails = tuple(Failure(text_of(f.get("customer_id")), text_of(f.get("at")), text_of(f.get("invoice_id")))
                  for f in failures or () if isinstance(f, Mapping))
    return parsed, fails


def _inbound(raw: Any) -> Inbound | None:
    if not isinstance(raw, Mapping):
        return None
    return Inbound(at=text_of(raw.get("at")), subject=text_of(raw.get("subject")), link=text_of(raw.get("link")),
                   sender=text_of(raw.get("from")), thread_id=text_of(raw.get("thread_id")),
                   quote=text_of(raw.get("quote")) or None)


def parse_mail(text: str) -> tuple[MailRecord, ...] | None:
    raw = _list(load_json_object(text), "mail")
    if raw is None:
        return None
    return tuple(
        MailRecord(account_id=text_of(m.get("account_id")),
                   inbound=tuple(i for i in (_inbound(x) for x in m.get("inbound") or ()) if i is not None)
                   if isinstance(m.get("inbound"), list) else (),
                   last_inbound_at=text_of(m.get("last_inbound_at")), last_sent_at=text_of(m.get("last_sent_at")))
        for m in raw if isinstance(m, Mapping))


def parse_tickets(text: str) -> tuple[Ticket, ...] | None:
    raw = _list(load_json_object(text), "tickets")
    if raw is None:
        return None
    return tuple(
        Ticket(account_id=text_of(t.get("account_id")), id=text_of(t.get("id")),
               priority=text_of(t.get("priority")).lower(), status=text_of(t.get("status")).lower(),
               subject=text_of(t.get("subject")), created_at=text_of(t.get("created_at")),
               link=text_of(t.get("link")))
        for t in raw if isinstance(t, Mapping))
