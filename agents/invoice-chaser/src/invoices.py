"""Pure data: the invoice, reminder and reply records, reading the model's JSON answers, what the user's
message asks for (filters, envelope input, pasted rows, json mode), and state (de)serialisation.
The checks that decide what survives live in checks.py; levels and ranking live in ladder.py."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any

_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_STATUSES = ("ok", "no_tool", "off_topic")

# Currencies whose amounts carry no minor unit (amounts are already whole units).
ZERO_DECIMAL = frozenset({"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd",
                          "vuv", "xaf", "xof", "xpf"})


@dataclass(frozen=True)
class Invoice:
    id: str
    number: str
    customer_id: str
    amount_due_minor: int
    currency: str
    due_date: str               # exactly as the tool returned it
    customer_email: str | None = None
    customer_name: str | None = None
    hosted_url: str | None = None
    collection_method: str = ""
    link: str | None = None
    status: str = "open"
    due: str = ""               # the due date as an ISO UTC date, set by the post-check

    @property
    def label(self) -> str:
        return self.number or self.id

    @property
    def customer(self) -> str:
        return self.customer_name or self.customer_email or self.customer_id or "unknown customer"

    @property
    def auto_charge(self) -> bool:
        return self.collection_method.casefold() == "charge_automatically"

    @property
    def due_day(self) -> date:
        return date.fromisoformat(self.due)


@dataclass(frozen=True)
class Reminder:
    invoice_id: str
    sent_at: str
    subject: str
    link: str
    thread_ref: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class Reply:
    invoice_id: str
    received_at: str
    sender: str
    subject: str
    link: str


@dataclass(frozen=True)
class InvoicesAnswer:
    status: str
    livemode: bool | None
    invoices: tuple[Invoice, ...]


@dataclass(frozen=True)
class RemindersAnswer:
    reminders: tuple[Reminder, ...]
    replies: tuple[Reply, ...]


@dataclass(frozen=True)
class Entry:
    """One listed invoice with its verified history. Levels, days and scores are derived in ladder.py."""

    invoice: Invoice
    key: str = ""                       # stable within one list; outcomes are keyed by it
    id: str = ""                        # the number shown in the list (#n)
    reminders: tuple[Reminder, ...] = ()
    reminders_checked: bool = False
    reply: Reply | None = None


@dataclass(frozen=True)
class Request:
    """What the user's message asks for, parsed in code."""

    min_days: int = 1
    min_amount: float | None = None     # major units of the invoice currency
    customer: str | None = None
    invoice_ids: tuple[str, ...] = ()   # from an envelope (`overdue[].invoice_id`)
    json_mode: bool = False
    pasted: tuple[Invoice, ...] = field(default=())
    pasted_text: str = ""


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
    return str(value).strip() if value is not None else ""


def _opt(value: Any) -> str | None:
    text = text_of(value)
    return text if text and text.casefold() not in ("null", "none") else None


def _minor(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = text_of(value)
    return int(text) if re.fullmatch(r"-?\d+", text) else None


def parse_invoices(text: str) -> InvoicesAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in _STATUSES:
        return None
    raw = data.get("invoices") or []
    if not isinstance(raw, list):
        return None
    livemode = data.get("livemode") if isinstance(data.get("livemode"), bool) else None
    return InvoicesAnswer(data["status"], livemode, tuple(i for i in (_invoice(r) for r in raw) if i is not None))


def _invoice(raw: Any) -> Invoice | None:
    if not isinstance(raw, Mapping):
        return None
    amount = _minor(raw.get("amount_due_minor"))
    if amount is None:
        return None
    return Invoice(id=text_of(raw.get("id")), number=_opt(raw.get("number")) or "",
                   customer_id=text_of(raw.get("customer_id")), amount_due_minor=amount,
                   currency=text_of(raw.get("currency")).upper(), due_date=_opt(raw.get("due_date")) or "",
                   customer_email=_opt(raw.get("customer_email")), customer_name=_opt(raw.get("customer_name")),
                   hosted_url=_opt(raw.get("hosted_url")), collection_method=text_of(raw.get("collection_method")),
                   link=_opt(raw.get("link")), status=text_of(raw.get("status")).casefold())


def parse_customers(text: str) -> dict[str, tuple[str | None, str | None]]:
    """customer id → (email, name), as the model claims them (checked in code)."""
    data = load_json_object(text)
    raw = data.get("customers") if data else None
    if not isinstance(raw, list):
        return {}
    return {text_of(c.get("customer_id")): (_opt(c.get("email")), _opt(c.get("name")))
            for c in raw if isinstance(c, Mapping) and text_of(c.get("customer_id"))}


def parse_reminders(text: str) -> RemindersAnswer | None:
    data = load_json_object(text)
    if data is None:
        return None
    reminders = tuple(Reminder(invoice_id=text_of(r.get("invoice_id")), sent_at=text_of(r.get("sent_at")),
                               subject=text_of(r.get("subject")), link=text_of(r.get("link")),
                               thread_ref=text_of(r.get("thread_ref")), evidence=text_of(r.get("evidence")))
                      for r in _items(data.get("reminders")))
    replies = tuple(Reply(invoice_id=text_of(r.get("invoice_id")), received_at=text_of(r.get("received_at")),
                          sender=text_of(r.get("from")), subject=text_of(r.get("subject")), link=text_of(r.get("link")))
                    for r in _items(data.get("replies")))
    return RemindersAnswer(reminders, replies)


def _items(value: Any) -> list[Mapping]:
    return [v for v in value if isinstance(v, Mapping)] if isinstance(value, list) else []


# --- the user's message ---------------------------------------------------------------------------

_MORE_THAN_DAYS = re.compile(r"\b(?:more than|over|older than|greater than|>)\s*(\d{1,3})\s*days?\b", re.IGNORECASE)
_AT_LEAST_DAYS = re.compile(r"\b(?:at least|>=)\s*(\d{1,3})\s*days?\b|\b(\d{1,3})\s*\+\s*days?\b"
                            r"|\b(\d{1,3})\s*days?\s*(?:or more|and (?:up|over|more))\b"
                            r"|\b(\d{1,3})\s*days?\s*(?:late|overdue|past due)\b", re.IGNORECASE)
_AMOUNT = re.compile(r"\b(?:above|over|more than|at least|greater than|>=?|min(?:imum)?)\s*[$€£]?\s*"
                     r"(\d[\d,]*(?:\.\d+)?)(?!\d|[,.]\d)\s*(k\b)?(?!\s*(?:days?|d\b|%))", re.IGNORECASE)
_CUSTOMER = re.compile(r"\b(?:only|just|for|from)\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,3})")
_NOT_CUSTOMERS = frozenset({"I", "Me", "The", "All", "Each", "Every", "Invoice", "Invoices", "Draft", "Today"})
_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b|\bin json\b|\bjson format\b", re.IGNORECASE)
_PASTE_ROW = re.compile(r"^\s*([^,]+),\s*([^,]*),\s*([^,\s]*@[^,\s]*|)\s*,\s*([\d.,]+)\s*,\s*([A-Za-z]{3})\s*,"
                        r"\s*(\d{4}-\d{2}-\d{2})\s*$")


def parse_request(query: str) -> Request:
    text = query or ""
    envelope_ids = envelope_invoice_ids(text)
    pasted = pasted_rows(text)
    return Request(min_days=min_days(text), min_amount=min_amount(text),
                   customer=None if pasted or envelope_ids else customer_filter(text),
                   invoice_ids=envelope_ids, json_mode=bool(_JSON_MODE.search(text)), pasted=pasted,
                   pasted_text=text if pasted else "")


def min_days(text: str) -> int:
    """"more than 14 days" → 15; "14+ days" / "at least 14 days" / "14 days late" → 14; default 1."""
    more = _MORE_THAN_DAYS.search(text)
    if more:
        return int(more.group(1)) + 1
    least = _AT_LEAST_DAYS.search(text)
    if least:
        return max(1, int(next(g for g in least.groups() if g)))
    return 1


def min_amount(text: str) -> float | None:
    match = _AMOUNT.search(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value * 1000 if match.group(2) else value


def customer_filter(text: str) -> str | None:
    for match in _CUSTOMER.finditer(text):
        name = match.group(1).strip(" .'")
        if name.split()[0] not in _NOT_CUSTOMERS and not re.search(r"\d", name):
            return name
    return None


def wants_json(text: str) -> bool:
    return bool(_JSON_MODE.search(text or ""))


def envelope_invoice_ids(text: str) -> tuple[str, ...]:
    """`{"overdue": [{"invoice_id": ...}]}` (or this agent's own `invoice`) from a previous step."""
    data = load_json_object(text)
    rows = data.get("overdue") if data else None
    if not isinstance(rows, list):
        return ()
    ids = (text_of(r.get("invoice_id") or r.get("invoice")) for r in rows if isinstance(r, Mapping))
    return tuple(dict.fromkeys(i for i in ids if i))


def pasted_rows(text: str) -> tuple[Invoice, ...]:
    """`invoice_number, customer, email, amount, currency, due_date` lines (amount in major units)."""
    rows: tuple[Invoice, ...] = ()
    for line in (text or "").splitlines():
        match = _PASTE_ROW.match(line)
        if not match:
            continue
        number, customer, email, amount, currency, due = (g.strip() for g in match.groups())
        minor = _to_minor(amount, currency)
        if minor is None:
            continue
        rows = (*rows, Invoice(id=number, number=number, customer_id="", amount_due_minor=minor,
                               currency=currency.upper(), due_date=due, customer_email=email or None,
                               customer_name=customer or None, collection_method="send_invoice"))
    return rows


def _to_minor(amount: str, currency: str) -> int | None:
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    return round(value if currency.casefold() in ZERO_DECIMAL else value * 100)


# --- state ----------------------------------------------------------------------------------------

def to_state(entries: Sequence[Entry]) -> list[dict]:
    return [asdict(e) for e in entries]


def from_state(raw: Sequence[Mapping[str, Any]] | None) -> tuple[Entry, ...]:
    return tuple(_entry(item) for item in raw or ())


def _entry(item: Mapping[str, Any]) -> Entry:
    invoice = Invoice(**{**dict(item["invoice"]), "amount_due_minor": int(item["invoice"]["amount_due_minor"])})
    reply = item.get("reply")
    return replace(Entry(invoice=invoice), key=str(item.get("key") or ""), id=str(item.get("id") or ""),
                   reminders=tuple(Reminder(**dict(r)) for r in item.get("reminders") or ()),
                   reminders_checked=bool(item.get("reminders_checked")),
                   reply=Reply(**dict(reply)) if isinstance(reply, Mapping) else None)
