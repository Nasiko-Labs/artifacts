"""Pure data: the case records (charge, customer, dispute, messages), reading the model's JSON answers, what
the user's message asks for (ids, email addresses, json mode, pasted facts, envelope input), and the refund
policy parameters read from org policy text. All money is integer minor units (cents) until rendering.

Checks live in checks.py; computed facts in facts.py; the rules in policy.py; the dispute checklist in
evidence.py."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict

PASTED_SOURCE = "pasted"
_CODE_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ID = re.compile(r"\b(?:ch|pi|py|dp|du|in)_[A-Za-z0-9]{3,}\b")
_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b", re.IGNORECASE)
_LIST_DISPUTES = re.compile(r"\b(?:list|show|which|any|what are)\b.{0,30}\bdisputes\b", re.IGNORECASE)
_DISPUTE_WORD = re.compile(r"\b(?:disputes?|chargebacks?)\b", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(?:refund\w*|disput\w*|chargebacks?|charg\w*|payments?|paid|customers?|invoic\w*|"
                     r"subscription\w*|money back|reimburs\w*|evidence|cancel\w*|billing|plans?)\b", re.IGNORECASE)
_NEVER_REFUNDS = re.compile(
    r"\b(?:issue|process|execute|make|do|perform|submit|send|trigger|run)\s+(?:the\s+|a\s+|this\s+|that\s+|her\s+|"
    r"his\s+|their\s+)?(?:full\s+|partial\s+)?refunds?\b"
    r"|\b(?:can|could|will|would|please)\s+(?:you\s+)?(?:please\s+)?refund\s+(?:it|this|that|them|her|him|the)\b"
    r"|\brefund\s+(?:it|this|that|them)\b"
    r"|\b(?:submit|send|upload)\s+(?:the\s+)?(?:dispute\s+)?evidence\b"
    r"|\b(?:close|accept|resolve)\s+(?:the\s+|this\s+)?dispute\b(?!.*\?)",
    re.IGNORECASE)
_CONFIRMED = re.compile(r"\b(?:i(?:'ve| have)?\s+(?:just\s+)?refunded|i(?:'ll| will)\s+refund|"
                        r"refund\s+(?:is\s+|was\s+|has\s+been\s+)?(?:done|issued|processed)|"
                        r"i\s+(?:issued|processed)\s+the\s+refund)\b", re.IGNORECASE)
_DRAFT_ASK = re.compile(r"\b(?:draft|write|prepare|compose)\b.{0,30}\b(?:reply|email|response|answer)\b"
                        r"|\b(?:reply|respond)\s+to\b", re.IGNORECASE)
_ZERO_DECIMAL = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF"})

# --- pasted facts ---------------------------------------------------------------------------------------
PASTED_KEYS = ("charge_id", "amount", "currency", "date", "plan", "customer_email", "prior_refunds")
_PASTED_LINE = re.compile(r"^\s*[-*]?\s*(charge[_ ]id|amount|currency|date|plan|customer[_ ]email|email|"
                          r"prior[_ ]refunds)\s*[:=]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class Charge(Record):
    id: str
    amount_minor: int
    currency: str
    created_at: str
    status: str = ""
    refunded_minor: int = 0
    paid: bool = True
    payment_intent: str | None = None
    description: str | None = None
    customer_id: str | None = None
    invoice_id: str | None = None
    card_brand: str | None = None
    card_country: str | None = None
    link: str | None = None
    source: str = "tool"


class Subscription(Record):
    id: str
    plan_name: str | None = None
    interval: str | None = None
    status: str = ""
    amount_minor: int | None = None
    created_at: str | None = None
    current_period_start: str | None = None
    current_period_end: str | None = None
    canceled_at: str | None = None
    link: str | None = None


class Invoice(Record):
    id: str
    number: str | None = None
    paid_at: str | None = None
    amount_minor: int | None = None
    link: str | None = None


class PriorRefund(Record):
    id: str
    amount_minor: int | None = None
    at: str | None = None
    reason: str | None = None
    charge_id: str | None = None


class PriorDispute(Record):
    id: str
    at: str | None = None
    status: str = ""
    charge_id: str | None = None


class Customer(Record):
    id: str
    email: str
    name: str | None = None


class CustomerFile(Record):
    customer: Customer | None = None
    subscriptions: tuple[Subscription, ...] = ()
    invoices: tuple[Invoice, ...] = ()
    prior_refunds: tuple[PriorRefund, ...] = ()
    prior_disputes: tuple[PriorDispute, ...] = ()
    prior_refund_count: int | None = None  # pasted facts only: a count with no rows


class Dispute(Record):
    id: str
    charge_id: str | None = None
    amount_minor: int
    currency: str
    reason: str = "general"
    status: str = ""
    evidence_due_by: str | None = None
    network_reason_code: str | None = None
    link: str | None = None


class Msg(Record):
    at: str
    direction: str
    subject: str = ""
    link: str | None = None
    quote: str | None = None
    thread_ref: str | None = None


class Candidate(Record):
    customer_id: str
    email: str
    name: str | None = None


@dataclass(frozen=True)
class PaymentAnswer:
    status: str
    charge: Charge | None
    recent_charges: tuple[Charge, ...]
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class Policy:
    """The refund policy the rules run on: built-in defaults, overridden field by field by org policy."""

    window_days: int = 14
    auto_approve_max_minor: int = 10_000
    annual_refund: str = "prorated"          # "prorated" | "no refund"
    source: str = "built-in defaults"
    text: str | None = None                  # the org policy paragraph, when one was found

    def to_dict(self) -> dict:
        return {"window_days": self.window_days, "auto_approve_max_minor": self.auto_approve_max_minor,
                "annual_refund": self.annual_refund, "source": self.source, "text": self.text}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Policy:
        return Policy(**{k: v for k, v in dict(data or {}).items() if k in Policy.__dataclass_fields__})


@dataclass(frozen=True)
class Request:
    """What the user's message asks for, parsed in code."""

    emails: tuple[str, ...] = ()
    ids: tuple[str, ...] = ()
    json_mode: bool = False
    list_disputes: bool = False
    mentions_dispute: bool = False

    @property
    def dispute_ids(self) -> tuple[str, ...]:
        return tuple(i for i in self.ids if i.startswith(("dp_", "du_")))

    @property
    def wants_dispute(self) -> bool:
        return bool(self.dispute_ids) or self.mentions_dispute

    def to_dict(self) -> dict:
        return {"emails": list(self.emails), "ids": list(self.ids), "json_mode": self.json_mode,
                "list_disputes": self.list_disputes, "mentions_dispute": self.mentions_dispute}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Request:
        raw = dict(data or {})
        return Request(emails=tuple(raw.get("emails") or ()), ids=tuple(raw.get("ids") or ()),
                       json_mode=bool(raw.get("json_mode")), list_disputes=bool(raw.get("list_disputes")),
                       mentions_dispute=bool(raw.get("mentions_dispute")))


# --- the user's message ---------------------------------------------------------------------------------

def parse_request(query: str, metadata: Mapping[str, Any] | None = None) -> Request:
    text = query or ""
    json_mode = bool(_JSON_MODE.search(text)) or str((metadata or {}).get("output") or "").lower() == "json"
    return Request(emails=tuple(dict.fromkeys(e.lower() for e in EMAIL.findall(text))),
                   ids=tuple(dict.fromkeys(_ID.findall(text))), json_mode=json_mode,
                   list_disputes=bool(_LIST_DISPUTES.search(text)), mentions_dispute=bool(_DISPUTE_WORD.search(text)))


def mentions_domain(query: str) -> bool:
    return bool(_DOMAIN.search(query or "")) or bool(_ID.search(query or ""))


def asks_to_issue(query: str) -> bool:
    """A request to move money or act on a dispute: answered in code, never acted on."""
    return bool(_NEVER_REFUNDS.search(query or ""))


def confirms_refund(query: str) -> bool:
    return bool(_CONFIRMED.search(query or ""))


def asks_to_draft(query: str) -> bool:
    return bool(_DRAFT_ASK.search(query or ""))


def envelope_input(text: str) -> str | None:
    """A previous step's envelope (JSON text) becomes a plain request: its summary plus every address and id
    in it. Fields read: `summary`, `items`, `customer_email`, `charge_id`, `dispute_id`."""
    data = load_json_object(text) if (text or "").lstrip().startswith(("{", "```")) else None
    if data is None or not ({"summary", "items"} & set(data)):
        return None
    flat = json.dumps(data, ensure_ascii=False)
    hints = list(dict.fromkeys([*EMAIL.findall(flat), *_ID.findall(flat)]))
    return " ".join(p for p in (str(data.get("summary") or "Refund request"), *hints) if p)


def pasted_facts(text: str) -> dict[str, str] | None:
    """`key: value` lines for the pasted-facts fallback; needs at least an amount and a date."""
    found: dict[str, str] = {}
    for match in _PASTED_LINE.finditer(text or ""):
        key = match.group(1).lower().replace(" ", "_")
        found = {**found, ("customer_email" if key == "email" else key): match.group(2)}
    return found if {"amount", "date"} <= set(found) else None


def to_minor(amount: str, currency: str = "USD") -> int | None:
    """"240.00" / "1,200" / "$40" → minor units, rounded half up; None when not a positive amount."""
    cleaned = re.sub(r"[^\d.]", "", amount or "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    scale = 1 if currency.upper() in _ZERO_DECIMAL else 100
    minor = int((value * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return minor if minor > 0 else None


def money(amount_minor: int | None, currency: str) -> str:
    if amount_minor is None:
        return "unknown"
    code = (currency or "").upper()
    if code in _ZERO_DECIMAL:
        return f"{amount_minor:,} {code}".strip()
    return f"{Decimal(amount_minor) / 100:,.2f} {code}".strip()


# --- the model's answers --------------------------------------------------------------------------------

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


def opt(value: Any) -> str | None:
    return text_of(value) or None


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


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return default


def charge_of(raw: Any) -> Charge | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")):
        return None
    amount = int_of(raw.get("amount_minor"))
    if amount is None:
        return None
    return Charge(id=text_of(raw.get("id")), amount_minor=amount, currency=text_of(raw.get("currency")).upper(),
                  created_at=text_of(raw.get("created_at")), status=text_of(raw.get("status")).lower(),
                  refunded_minor=max(0, int_of(raw.get("refunded_minor")) or 0), paid=_bool(raw.get("paid"), True),
                  payment_intent=opt(raw.get("payment_intent")), description=opt(raw.get("description")),
                  customer_id=opt(raw.get("customer_id")), invoice_id=opt(raw.get("invoice_id")),
                  card_brand=opt(raw.get("card_brand")), card_country=opt(raw.get("card_country")),
                  link=opt(raw.get("link")))


def parse_payment(text: str) -> PaymentAnswer | None:
    data = load_json_object(text)
    if data is None:
        return None
    status = text_of(data.get("status")) or "ok"
    if status not in ("ok", "not_found", "ambiguous", "off_topic", "no_read_tool"):
        return None
    recent = data.get("recent_charges") if isinstance(data.get("recent_charges"), list) else []
    raw_candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    candidates = tuple(Candidate(customer_id=text_of(c.get("customer_id")), email=text_of(c.get("email")),
                                 name=opt(c.get("name")))
                       for c in raw_candidates if isinstance(c, Mapping) and text_of(c.get("email")))
    return PaymentAnswer(status=status, charge=charge_of(data.get("charge")),
                         recent_charges=tuple(c for c in (charge_of(r) for r in recent) if c is not None),
                         candidates=candidates)


def _rows(data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    raw = data.get(key)
    return [r for r in raw if isinstance(r, Mapping) and text_of(r.get("id"))] if isinstance(raw, list) else []


def parse_customer(text: str) -> CustomerFile | None:
    data = load_json_object(text)
    if data is None:
        return None
    raw = data.get("customer")
    customer = (Customer(id=text_of(raw.get("id")), email=text_of(raw.get("email")).lower(), name=opt(raw.get("name")))
                if isinstance(raw, Mapping) and text_of(raw.get("id")) and text_of(raw.get("email")) else None)
    subs = tuple(Subscription(id=text_of(r["id"]), plan_name=opt(r.get("plan_name")),
                              interval=text_of(r.get("interval")).lower() or None, status=text_of(r.get("status")).lower(),
                              amount_minor=int_of(r.get("amount_minor")), created_at=opt(r.get("created_at")),
                              current_period_start=opt(r.get("current_period_start")),
                              current_period_end=opt(r.get("current_period_end")),
                              canceled_at=opt(r.get("canceled_at")), link=opt(r.get("link")))
                 for r in _rows(data, "subscriptions"))
    invoices = tuple(Invoice(id=text_of(r["id"]), number=opt(r.get("number")), paid_at=opt(r.get("paid_at")),
                             amount_minor=int_of(r.get("amount_minor")), link=opt(r.get("link")))
                     for r in _rows(data, "invoices"))
    refunds = tuple(PriorRefund(id=text_of(r["id"]), amount_minor=int_of(r.get("amount_minor")), at=opt(r.get("at")),
                                reason=opt(r.get("reason")), charge_id=opt(r.get("charge_id")))
                    for r in _rows(data, "prior_refunds"))
    disputes = tuple(PriorDispute(id=text_of(r["id"]), at=opt(r.get("at")), status=text_of(r.get("status")).lower(),
                                  charge_id=opt(r.get("charge_id")))
                     for r in _rows(data, "prior_disputes"))
    return CustomerFile(customer=customer, subscriptions=subs, invoices=invoices, prior_refunds=refunds,
                        prior_disputes=disputes)


def dispute_of(raw: Any) -> Dispute | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("id")):
        return None
    amount = int_of(raw.get("amount_minor"))
    if amount is None:
        return None
    return Dispute(id=text_of(raw.get("id")), charge_id=opt(raw.get("charge_id")), amount_minor=amount,
                   currency=text_of(raw.get("currency")).upper(), reason=text_of(raw.get("reason")).lower() or "general",
                   status=text_of(raw.get("status")).lower(), evidence_due_by=opt(raw.get("evidence_due_by")),
                   network_reason_code=opt(raw.get("network_reason_code")), link=opt(raw.get("link")))


def parse_disputes(text: str) -> tuple[Dispute, ...] | None:
    data = load_json_object(text)
    if data is None:
        return None
    raw = data.get("disputes")
    if isinstance(raw, list):
        return tuple(d for d in (dispute_of(r) for r in raw) if d is not None)
    single = dispute_of(data.get("dispute"))
    return (single,) if single else ()


def parse_messages(text: str) -> tuple[Msg, ...] | None:
    data = load_json_object(text)
    raw = data.get("messages") if data else None
    if not isinstance(raw, list):
        return None
    out = []
    for r in raw:
        if not isinstance(r, Mapping) or not text_of(r.get("at")):
            continue
        direction = text_of(r.get("direction")).lower()
        out.append(Msg(at=text_of(r.get("at")), direction=direction if direction in ("in", "out") else "in",
                       subject=text_of(r.get("subject")), link=opt(r.get("link")), quote=opt(r.get("quote")),
                       thread_ref=opt(r.get("thread_ref"))))
    return tuple(out)


# --- org policy -----------------------------------------------------------------------------------------

_POLICY_WINDOW = re.compile(r"refund window\s*(?:is|of|:|=)?\s*(\d{1,4})\s*days?", re.IGNORECASE)
_POLICY_ANNUAL = re.compile(r"annual plans?\s*(?:are|:|=|-)?\s*(prorated|no refunds?)\b", re.IGNORECASE)
_POLICY_MAX = re.compile(r"max(?:imum)?\s+auto[- ]approve\s*(?:is|of|:|=)?\s*([$€£]?\s*[\d,]+(?:\.\d{1,2})?)",
                         re.IGNORECASE)
MAX_POLICY_WINDOW_DAYS = 365


def parse_policy(org_text: str | None, currency: str = "USD") -> Policy:
    """The org policy's "refund policy" paragraph, parsed in code; each field that is missing or malformed
    keeps its default."""
    paragraph = _policy_paragraph(org_text or "")
    if paragraph is None:
        return Policy()
    policy = Policy(source="your organization policy", text=paragraph)
    window = _POLICY_WINDOW.search(paragraph)
    if window and 0 < int(window.group(1)) <= MAX_POLICY_WINDOW_DAYS:
        policy = replace(policy, window_days=int(window.group(1)))
    annual = _POLICY_ANNUAL.search(paragraph)
    if annual:
        policy = replace(policy, annual_refund="prorated" if annual.group(1).lower() == "prorated" else "no refund")
    cap = _POLICY_MAX.search(paragraph)
    minor = to_minor(cap.group(1), currency) if cap else None
    if minor is not None:
        policy = replace(policy, auto_approve_max_minor=minor)
    return policy


def _policy_paragraph(text: str) -> str | None:
    for block in re.split(r"\n\s*\n", text):
        if "refund policy" in block.casefold():
            return block.strip()[:2000]
    return None
