"""Pure data: the failure record, reading the model's JSON answers, what the user's message asks for
(window, filters, channel, "draft for #n", json mode, envelope input, pasted rows), and state
(de)serialisation. Checks live in checks.py; classification in classify.py; ranking in scoring.py.

All money is integer minor units (cents) until rendering."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

Status = Literal["ok", "no_read_tool", "off_topic"]
_STATUSES = ("ok", "no_read_tool", "off_topic")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

PASTED_PREFIX = "pasted-"
DEFAULT_WINDOW_DAYS = 7
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 30
_SECONDS_PER_DAY = 86400
_DAYS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|three|four|a)\s*weeks?\b", re.IGNORECASE)
_MONTH = re.compile(r"\b(?:a|one|1|last|past)\s+month\b", re.IGNORECASE)
_YESTERDAY = re.compile(r"\b(?:since\s+)?(?:yesterday|today|last 24 hours)\b", re.IGNORECASE)
_LAST_RUN = re.compile(r"\bsince\s+(?:my|the)?\s*last\s+(?:run|check|time)\b", re.IGNORECASE)
_WORD_NUMBERS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}
_MIN_AMOUNT = re.compile(r"\b(?:above|over|more than|at least|greater than|>=?)\s*([$€£]?)\s*(\d[\d,]*(?:\.\d{1,2})?)\s*"
                         r"([A-Za-z]{3})?\b", re.IGNORECASE)
_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
KNOWN_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CAD", "AUD", "INR", "JPY", "CHF", "SEK", "NZD", "SGD", "BRL",
                              "MXN", "DKK", "NOK", "PLN", "HKD", "ZAR", "KRW"})
_CURRENCY = re.compile(r"\b(?:only|just|in)\s+([A-Za-z]{3})\b", re.IGNORECASE)
_CHANNEL = re.compile(r"(?<![\w&])#([a-z0-9][a-z0-9._-]{0,79})\b", re.IGNORECASE)
_NUMBERED = re.compile(r"#\s*(\d{1,3})\b")
_DRAFT_ASK = re.compile(r"\b(?:draft|write|prepare|email|send)\b", re.IGNORECASE)
_POST_ASK = re.compile(r"\b(?:post|share|send)\b.*\b(?:high[- ]value|digest|biggest|channel|to\s+#)", re.IGNORECASE)
_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DOMAIN = re.compile(r"\b(?:pay\w*|invoic\w*|charg\w*|fail\w*|declin\w*|cards?|customers?|dunning|recover\w*|"
                     r"rescu\w*|billing|subscription\w*|retr(?:y|ies)|refunds?|disputes?|digest|drafts?|"
                     r"high[- ]value|run)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Failure:
    """One failed payment. The first block comes from the payments read (checked verbatim), the second
    from the history and contact steps (code-recomputed), `key`/`id` from code."""

    id: str
    customer_id: str
    customer_email: str
    amount_minor: int
    currency: str
    failed_at: str
    invoice_id: str | None = None
    customer_name: str | None = None
    decline_code: str | None = None
    failure_message: str | None = None
    attempt_count: int | None = None
    next_attempt_at: str | None = None
    plan_name: str | None = None
    link: str | None = None
    # history (enrich step)
    history_checked: bool = False
    monthly_minor: int | None = None       # subscription amount per month, normalised in code
    successful_payments: int = 0
    prior_failures: int = 0
    lifetime_paid_minor: int = 0           # sum of successful payments in this failure's currency
    disputes: int = 0
    refunds: int = 0
    # contact (email step)
    contact_checked: bool = False
    contacted_at: str | None = None
    contact_link: str | None = None
    # list position
    key: str = ""
    number: str = ""

    @property
    def who(self) -> str:
        return self.customer_name or self.customer_email

    @property
    def pasted(self) -> bool:
        return self.id.startswith(PASTED_PREFIX)


@dataclass(frozen=True)
class FailuresAnswer:
    status: Status
    failures: tuple[Failure, ...]


@dataclass(frozen=True)
class PaymentRow:
    """One payment in a customer's history, as the model copied it from a tool."""

    id: str
    status: str
    amount_minor: int | None
    currency: str


@dataclass(frozen=True)
class CustomerHistory:
    customer_id: str
    plan_name: str | None
    plan_amount_minor: int | None
    interval: str | None
    interval_count: int
    rows: tuple[PaymentRow, ...]
    successful_payments: int | None
    prior_failures: int | None
    disputes: int
    refunds: int
    dispute_ids: tuple[str, ...]
    refund_ids: tuple[str, ...]


@dataclass(frozen=True)
class Contact:
    customer_email: str
    last_sent_at: str | None
    link: str | None


@dataclass(frozen=True)
class Request:
    """What the user's message asks for, parsed in code."""

    window_days: int = DEFAULT_WINDOW_DAYS
    min_amount_minor: int | None = None
    currency: str | None = None
    channel: str | None = None
    since_last_run: bool = False
    json_mode: bool = False
    customers: tuple[str, ...] = ()   # from an envelope: only these customers


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


def int_of(value: Any) -> int | None:
    """An integer, or None. Floats with a fraction and booleans are not integers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def parse_failures(text: str) -> FailuresAnswer | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in _STATUSES:
        return None
    raw = data.get("failures") or []
    if not isinstance(raw, list):
        return None
    return FailuresAnswer(status=data["status"],
                          failures=tuple(f for f in (_failure(item) for item in raw) if f is not None))


def _failure(raw: Any) -> Failure | None:
    if not isinstance(raw, Mapping):
        return None
    amount = int_of(raw.get("amount_minor"))
    if amount is None:
        return None
    return Failure(
        id=text_of(raw.get("id")), customer_id=text_of(raw.get("customer_id")),
        customer_email=text_of(raw.get("customer_email")), amount_minor=amount,
        currency=text_of(raw.get("currency")).upper(), failed_at=text_of(raw.get("failed_at")),
        invoice_id=text_of(raw.get("invoice_id")) or None, customer_name=text_of(raw.get("customer_name")) or None,
        decline_code=text_of(raw.get("decline_code")).lower() or None,
        failure_message=text_of(raw.get("failure_message")) or None, attempt_count=int_of(raw.get("attempt_count")),
        next_attempt_at=text_of(raw.get("next_attempt_at")) or None, plan_name=text_of(raw.get("plan_name")) or None,
        link=text_of(raw.get("link")) or None,
    )


def parse_histories(text: str) -> tuple[CustomerHistory, ...]:
    data = load_json_object(text)
    raw = data.get("customers") if data else None
    if not isinstance(raw, list):
        return ()
    return tuple(h for h in (_history(item) for item in raw) if h is not None)


def _history(raw: Any) -> CustomerHistory | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("customer_id")):
        return None
    rows = raw.get("payments") if isinstance(raw.get("payments"), list) else []
    return CustomerHistory(
        customer_id=text_of(raw.get("customer_id")), plan_name=text_of(raw.get("plan_name")) or None,
        plan_amount_minor=int_of(raw.get("plan_amount_minor")), interval=text_of(raw.get("interval")).lower() or None,
        interval_count=max(1, int_of(raw.get("interval_count")) or 1),
        rows=tuple(r for r in (_row(item) for item in rows) if r is not None),
        successful_payments=int_of(raw.get("successful_payments")), prior_failures=int_of(raw.get("prior_failures")),
        disputes=max(0, int_of(raw.get("disputes")) or 0), refunds=max(0, int_of(raw.get("refunds")) or 0),
        dispute_ids=_ids(raw.get("dispute_ids")), refund_ids=_ids(raw.get("refund_ids")),
    )


def _ids(value: Any) -> tuple[str, ...]:
    return tuple(t for t in (text_of(v) for v in value) if t) if isinstance(value, list) else ()


def _row(raw: Any) -> PaymentRow | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")):
        return None
    return PaymentRow(id=text_of(raw.get("id")), status=text_of(raw.get("status")).lower(),
                      amount_minor=int_of(raw.get("amount_minor")), currency=text_of(raw.get("currency")).upper())


def parse_contacts(text: str) -> tuple[Contact, ...]:
    data = load_json_object(text)
    raw = data.get("contacts") if data else None
    if not isinstance(raw, list):
        return ()
    return tuple(Contact(text_of(c.get("customer_email")), text_of(c.get("last_sent_at")) or None,
                         text_of(c.get("link")) or None)
                 for c in raw if isinstance(c, Mapping) and text_of(c.get("customer_email")))


def parse_channels(text: str) -> tuple[str, ...]:
    data = load_json_object(text)
    raw = data.get("channels") if data else None
    return tuple(dict.fromkeys(t.lstrip("#") for t in (text_of(c) for c in raw) if t)) if isinstance(raw, list) else ()


# --- the user's message ---------------------------------------------------------------------------

def parse_request(query: str, metadata: Mapping[str, Any] | None = None) -> Request:
    text = query or ""
    envelope = envelope_input(text)
    window = window_days(text)
    customers: tuple[str, ...] = ()
    if envelope is not None:
        window = _clamp(int_of(envelope.get("window_days")) or window)
        customers = tuple(t for t in (text_of(_customer_of(f)) for f in envelope.get("failures") or ()) if t)
    minimum, symbol_currency = min_amount(text)
    json_mode = bool(_JSON_MODE.search(text)) or text_of((metadata or {}).get("output")).lower() == "json"
    return Request(window_days=window, min_amount_minor=minimum, currency=currency_filter(text) or symbol_currency,
                   channel=channel_from(text), since_last_run=bool(_LAST_RUN.search(text)), json_mode=json_mode,
                   customers=customers)


def _customer_of(item: Any) -> Any:
    if not isinstance(item, Mapping):
        return None
    customer = item.get("customer")
    return customer.get("email") or customer.get("name") if isinstance(customer, Mapping) else customer


def envelope_input(text: str) -> dict | None:
    """A JSON envelope from an earlier workflow step: an object with a `failures` list or `window_days`."""
    stripped = (text or "").strip()
    if not stripped.startswith(("{", "```")):
        return None
    data = load_json_object(stripped)
    if data is None or not (isinstance(data.get("failures"), list) or "window_days" in data):
        return None
    return data


def window_days(query: str) -> int:
    """The window the user asked for: 7 days by default, clamped to 1-30."""
    text = query or ""
    days = _DAYS.search(text)
    if days:
        return _clamp(int(days.group(1)))
    weeks = _WEEKS.search(text)
    if weeks:
        word = weeks.group(1).lower()
        return _clamp(7 * (_WORD_NUMBERS.get(word) or int(word)))
    if _YESTERDAY.search(text):
        return MIN_WINDOW_DAYS
    return MAX_WINDOW_DAYS if _MONTH.search(text) else DEFAULT_WINDOW_DAYS


def days_since(last_run: datetime, now: datetime) -> int:
    """Whole days from the previous run to now, rounded up, clamped to the window limits."""
    seconds = max(0.0, (now - last_run).total_seconds())
    return _clamp(-(-int(seconds) // _SECONDS_PER_DAY))


def _clamp(days: int) -> int:
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, days))


def min_amount(query: str) -> tuple[int | None, str | None]:
    """"only above $500" → (50000, "USD"). Two-decimal minor units."""
    match = _MIN_AMOUNT.search(query or "")
    if not match:
        return None, None
    minor = to_minor(match.group(2).replace(",", ""))
    code = (match.group(3) or "").upper()
    currency = code if code in KNOWN_CURRENCIES else _SYMBOLS.get(match.group(1))
    return minor, currency


def currency_filter(query: str) -> str | None:
    match = _CURRENCY.search(query or "")
    code = match.group(1).upper() if match else ""
    return code if code in KNOWN_CURRENCIES else None


def channel_from(query: str) -> str | None:
    """`post to #finance-alerts` → "finance-alerts". List numbers (`#2`) are not channels."""
    for match in _CHANNEL.finditer(query or ""):
        name = match.group(1)
        if not name.isdigit():
            return name.lower()
    return None


def mentions_domain(query: str) -> bool:
    """Code-side scope check for the paths that end before any model call (no payments tool, gateway
    down): a message with no payment word at all is declined as off topic instead."""
    return bool(_DOMAIN.search(query or ""))


def numbers_in(query: str) -> tuple[str, ...]:
    """`draft for #1 and #3` → ("1", "3"), de-duplicated in order."""
    return tuple(dict.fromkeys(_NUMBERED.findall(query or "")))


def asks_to_draft(query: str) -> bool:
    return bool(_DRAFT_ASK.search(query or "")) and bool(numbers_in(query)) and not asks_to_post(query)


def asks_to_post(query: str) -> bool:
    return bool(_POST_ASK.search(query or "")) or bool(channel_from(query) and re.search(r"\bpost\b", query or "",
                                                                                         re.IGNORECASE))


def to_minor(amount: str) -> int | None:
    """"1,200.50" → 120050, rounded half up to two decimals. None when not a number."""
    try:
        value = Decimal(amount.replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# --- pasted rows ------------------------------------------------------------------------------------

def pasted_rows(text: str) -> tuple[Failure, ...]:
    """Lines `customer, email, amount, currency, decline_code, date` (a header line is skipped). A line
    without a valid address, a positive amount or a date is ignored."""
    rows: tuple[Failure, ...] = ()
    for n, line in enumerate((text or "").splitlines(), start=1):
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 6 or not _EMAIL.fullmatch(cells[1]):
            continue
        minor = to_minor(cells[2].lstrip("$€£"))
        if minor is None or minor <= 0:
            continue
        row_id = f"{PASTED_PREFIX}{n}"
        rows = (*rows, Failure(id=row_id, customer_id=cells[1].lower(), customer_email=cells[1],
                               amount_minor=minor, currency=cells[3].upper(), failed_at=cells[5],
                               customer_name=cells[0] or None, decline_code=cells[4].lower() or None))
    return rows


# --- state ------------------------------------------------------------------------------------------

def to_state(items: Sequence[Failure]) -> list[dict]:
    return [asdict(item) for item in items]


def from_state(raw: Sequence[Mapping[str, Any]] | None) -> tuple[Failure, ...]:
    names = {f.name for f in fields(Failure)}
    return tuple(Failure(**{k: v for k, v in item.items() if k in names}) for item in raw or ())
