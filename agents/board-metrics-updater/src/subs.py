"""Pure data: subscription records, reading the model's JSON answers (checked against tool output),
pasted rows, and what the user's message asks for (period, sheet/doc names, currency, write intent,
json mode). All money is integer minor units (cents) until rendering."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import checks

PASTED_PREFIX = "pasted-"
UNSUPPORTED_SCHEMES = frozenset({"tiered", "volume", "graduated"})
_INTERVALS = frozenset({"day", "week", "month", "year"})
_QUOTED = re.compile(r"[\"“']([^\"”']{2,80})[\"”']")
_AFTER_KEYWORD = re.compile(r"\b(?:sheet|spreadsheet|doc|document)\s+(?:named\s+|called\s+)?([A-Z0-9][\w &\-]{1,60})")
_TO_NAME = re.compile(r"\b(?:to|into|in)\s+(?:my\s+|the\s+|our\s+)?([A-Z0-9][\w &\-]{1,60}?)"
                      r"(?:\s+(?:sheet|spreadsheet|doc|document|tab))?\s*[.!?]?\s*$")
_GENERIC_NAMES = frozenset({"it", "sheet", "the sheet", "my sheet", "doc", "the doc", "document", "spreadsheet",
                            "this", "that", "there"})
_WRITE_VERB = re.compile(r"\b(?:add|write|append|put|push|update|record|log|save|insert)\b", re.IGNORECASE)
_DOC_APPEND_VERB = re.compile(r"\b(?:add|append|put|push|insert|save)\b", re.IGNORECASE)
_DOC_WORD = re.compile(r"\b(?:paragraph|narrative|docs?|document)\b", re.IGNORECASE)
_DOC_TARGET = re.compile(r"\b(?:docs?|document)\b", re.IGNORECASE)
_SHEET_WORD = re.compile(r"\b(?:sheet|spreadsheet|row)\b", re.IGNORECASE)
_CURRENCY = re.compile(r"\b(?:only|just|in)\s+([A-Za-z]{3})\b")
_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b", re.IGNORECASE)
_NUMBER_EDIT = re.compile(r"\b(?:change|set|make|edit|replace|override|correct|adjust|update)\b[^.?!]*?"
                          r"\b(?:mrr|arr|nrr|grr|churn|expansion|contraction|new mrr|customers|number|value|figure)\b"
                          r"[^.?!]*?\d", re.IGNORECASE)
_PERIOD_WORDS = re.compile(r"\b(?:month|quarter|q[1-4]|days?|weeks?|ytd|mtd|qtd|\d{4}-\d{2}-\d{2}|january|february|"
                           r"march|april|june|july|august|september|october|november|december)\b", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(?:mrr|arr|nrr|grr|revenue|churn|retention|metrics?|board|subscriptions?|customers?|"
                     r"expansion|contraction|bridge|sheet|paragraph|narrative|payments?)\b", re.IGNORECASE)
KNOWN_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CAD", "AUD", "INR", "JPY", "CHF", "SEK", "NZD", "SGD", "BRL",
                              "MXN", "DKK", "NOK", "PLN", "HKD", "ZAR"})


@dataclass(frozen=True)
class Item:
    price_id: str
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
    cancel_at_period_end: bool = False
    trial_end: datetime | None = None
    percent_off: Decimal | None = None
    amount_off_minor: int | None = None
    discount_duration: str | None = None

    @property
    def ends_at(self) -> datetime | None:
        """When it stops paying: `ended_at`, or the cancel time of a canceled one with no end time."""
        if self.ended_at is not None:
            return self.ended_at
        return self.canceled_at if self.status == "canceled" else None


@dataclass(frozen=True)
class Change:
    subscription_id: str
    at: datetime
    previous_items: tuple[Item, ...]


@dataclass(frozen=True)
class SubsAnswer:
    status: str                      # ok | no_read_tool | off_topic
    raw: tuple[dict, ...]
    has_more: bool = False


@dataclass(frozen=True)
class Request:
    """What the user's message asks for, parsed in code."""

    period_text: str = ""
    names_period: bool = False
    currency: str | None = None
    json_mode: bool = False
    wants_sheet: bool = False
    wants_doc: bool = False
    target_name: str | None = None
    number_edit: bool = False
    extra: dict = field(default_factory=dict)


# --- the model's answers ------------------------------------------------------------------------------

def parse_subs_answer(text: str) -> SubsAnswer | None:
    data = checks.load_json_object(text)
    if data is None:
        return None
    status = str(data.get("status") or "ok")
    raw = data.get("subscriptions")
    if status not in ("ok", "no_read_tool", "off_topic") or not isinstance(raw, list):
        return None
    return SubsAnswer(status, tuple(r for r in raw if isinstance(r, Mapping)), bool(data.get("has_more")))


def parse_changes_answer(text: str) -> tuple[dict, ...] | None:
    data = checks.load_json_object(text)
    raw = data.get("changes") if data else None
    return tuple(r for r in raw if isinstance(r, Mapping)) if isinstance(raw, list) else None


def check_subs(raw: tuple[dict, ...], seen_raw: str) -> tuple[tuple[Sub, ...], int]:
    """(subscriptions every id, amount and time of which is in the tool output, how many were dropped)."""
    seen = checks.normalise_text(seen_raw)
    kept: tuple[Sub, ...] = ()
    for record in raw:
        sub = _sub(record, seen_raw, seen)
        kept = kept if sub is None or any(s.id == sub.id for s in kept) else (*kept, sub)
    return kept, len(raw) - len(kept)


def check_changes(raw: tuple[dict, ...], seen_raw: str, known: set[str]) -> tuple[Change, ...]:
    seen = checks.normalise_text(seen_raw)
    out: tuple[Change, ...] = ()
    for record in raw:
        sub_id = checks.text_of(record.get("subscription_id"))
        at = checks.parse_time(record.get("at"))
        items = _items(record.get("previous_items"), seen_raw, seen)
        if sub_id in known and checks.time_backed(record.get("at"), at, seen_raw, seen) and items is not None:
            out = (*out, Change(sub_id, at, items))
    return out


def _sub(r: Mapping[str, Any], raw: str, seen: str) -> Sub | None:
    sub_id, customer = checks.text_of(r.get("id")), checks.text_of(r.get("customer_id"))
    created = checks.parse_time(r.get("created_at"))
    if not (checks.appears_in(sub_id, seen) and checks.appears_in(customer, seen)):
        return None
    if not checks.time_backed(r.get("created_at"), created, raw, seen):
        return None
    times = {}
    for key in ("canceled_at", "ended_at", "trial_end"):
        value = r.get(key)
        parsed = checks.parse_time(value) if value not in (None, "", "null") else None
        if value not in (None, "", "null") and not checks.time_backed(value, parsed, raw, seen):
            return None
        times[key] = parsed
    items = _items(r.get("quantity_items") or r.get("items"), raw, seen)
    interval = checks.text_of(r.get("interval")).casefold()
    if items is None or interval not in _INTERVALS:
        return None
    amount_off = checks.int_of(r.get("amount_off_minor"))
    if amount_off is not None and not checks.number_in(amount_off, raw):
        return None
    return Sub(id=sub_id, customer_id=customer, status=checks.text_of(r.get("status")).casefold() or "active",
               currency=checks.text_of(r.get("currency")).upper() or "USD", interval=interval,
               interval_count=max(1, checks.int_of(r.get("interval_count")) or 1), items=items, created_at=created,
               canceled_at=times["canceled_at"], ended_at=times["ended_at"],
               cancel_at_period_end=bool(r.get("cancel_at_period_end")), trial_end=times["trial_end"],
               percent_off=_percent(r.get("percent_off"), raw), amount_off_minor=amount_off,
               discount_duration=checks.text_of(r.get("discount_duration")).casefold() or None)


def _items(raw_items: Any, raw: str, seen: str) -> tuple[Item, ...] | None:
    if not isinstance(raw_items, list) or not raw_items:
        return None
    out: tuple[Item, ...] = ()
    for r in raw_items:
        if not isinstance(r, Mapping):
            return None
        amount = checks.int_of(r.get("unit_amount_minor"))
        if amount is not None and not checks.number_in(amount, raw):
            return None  # an amount the tools never returned
        out = (*out, Item(price_id=checks.text_of(r.get("price_id")), unit_amount_minor=amount,
                          quantity=max(0, checks.int_of(r.get("quantity")) or 1),
                          billing_scheme=checks.text_of(r.get("billing_scheme")).casefold() or "per_unit",
                          usage_type=checks.text_of(r.get("usage_type")).casefold() or "licensed"))
    return out


def _percent(value: Any, raw: str) -> Decimal | None:
    if value in (None, "", "null") or isinstance(value, bool):
        return None
    try:
        pct = Decimal(str(value))
    except InvalidOperation:
        return None
    text = format(pct.normalize(), "f")
    return pct if 0 < pct <= 100 and (text in raw or str(value) in raw) else None


# --- pasted rows --------------------------------------------------------------------------------------

def pasted_subs(text: str) -> tuple[Sub, ...]:
    """Rows `subscription_id, customer, status, currency, monthly_amount, started_at, ended_at`."""
    subs: tuple[Sub, ...] = ()
    for line in (text or "").splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != 7 or cells[0].casefold() == "subscription_id":
            continue
        sub = _pasted(cells)
        subs = subs if sub is None else (*subs, sub)
    return subs


def _pasted(cells: list[str]) -> Sub | None:
    sub_id, customer, status, currency, amount, started, ended = cells
    created = checks.parse_time(started)
    end = checks.parse_time(ended) if ended else None
    try:
        minor = int((Decimal(amount.replace(" ", "")) * 100).to_integral_value())
    except InvalidOperation:
        return None
    if not sub_id or not customer or created is None or (ended and end is None) or minor < 0:
        return None
    return Sub(id=f"{PASTED_PREFIX}{sub_id}", customer_id=customer, status=status.casefold() or "active",
               currency=currency.upper() or "USD", interval="month", interval_count=1,
               items=(Item(price_id="pasted", unit_amount_minor=minor),), created_at=created, ended_at=end)


# --- the request ----------------------------------------------------------------------------------------

def parse_request(query: str, metadata: Mapping[str, Any] | None = None) -> Request:
    meta = dict(metadata or {})
    text = query or ""
    period_text = str(meta.get("period") or _without_rows(text))
    doc_intent = bool(_DOC_WORD.search(text)) and bool(_DOC_APPEND_VERB.search(text) or _DOC_TARGET.search(text))
    name = target_name(text)
    sheet_intent = bool(_WRITE_VERB.search(text)) and bool(_SHEET_WORD.search(text) or name) and not doc_intent
    currency = next((c.upper() for c in _CURRENCY.findall(text) if c.upper() in KNOWN_CURRENCIES), None)
    json_mode = bool(_JSON_MODE.search(text)) or str(meta.get("output") or "").casefold() == "json"
    return Request(period_text=period_text, names_period=bool(_PERIOD_WORDS.search(period_text)), currency=currency,
                   json_mode=json_mode, wants_sheet=sheet_intent, wants_doc=doc_intent, target_name=name,
                   number_edit=bool(_NUMBER_EDIT.search(text)))


def _without_rows(text: str) -> str:
    """The message minus pasted rows, so a row's dates are never read as the period."""
    return "\n".join(line for line in text.splitlines() if line.count(",") != 6)


def target_name(text: str) -> str | None:
    """A sheet or doc name: quoted, after "sheet"/"doc", or "to <Name>" at the end of the message."""
    for pattern in (_QUOTED, _AFTER_KEYWORD, _TO_NAME):
        match = pattern.search(text or "")
        if match:
            name = re.sub(r"\s+(?:sheet|spreadsheet|doc|document|tab)$", "", match.group(1).strip(" .!?"),
                          flags=re.IGNORECASE)
            if name.casefold() not in _GENERIC_NAMES and len(name) >= 2:
                return name
    return None


def mentions_domain(text: str) -> bool:
    return bool(_DOMAIN.search(text or ""))
