"""The code post-check: the guarantee, not the prompt.

Every id, address and link the model returns must appear verbatim in this turn's tool output (or in the
user's own pasted text); every amount must be an integer found there. A record whose id or amount is not
backed is dropped; an optional field that is not verbatim becomes None. A message quote that is not
verbatim is removed (the message stays only with a verbatim link or thread reference)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from case import (
    EMAIL,
    Candidate,
    Charge,
    Customer,
    CustomerFile,
    Dispute,
    Invoice,
    Msg,
    PriorDispute,
    PriorRefund,
    Subscription,
)

MAX_MESSAGES = 10
MAX_CANDIDATES = 10
_EPOCH = re.compile(r"^\s*(\d{9,11})(?:\.\d+)?\s*$")
_WHITESPACE = re.compile(r"\s+")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_ESCAPES = (("\\n", " "), ("\\r", " "), ("\\t", " "), ('\\"', '"'), ("\\/", "/"), ("\\\\", "\\"))
_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', " ": " "})
_EDGE_PUNCTUATION = " .,;:!?\"'"
_LIVEMODE = re.compile(r"[\"']?livemode[\"']?\s*[:=]\s*(true|false)", re.IGNORECASE)
_INTERVALS = frozenset({"day", "week", "month", "year"})


def normalise_text(text: str) -> str:
    """Case, whitespace, curly quotes and JSON escapes do not change what a value says."""
    unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    for escaped, plain in _JSON_ESCAPES:
        unescaped = unescaped.replace(escaped, plain)
    return _WHITESPACE.sub(" ", unescaped.translate(_LOOKALIKES)).strip().casefold()


def appears_in(needle: str | None, seen: str) -> bool:
    """`needle` (an id, address, link or quote) occurs in normalised text. Empty never matches."""
    wanted = normalise_text(needle or "").strip(_EDGE_PUNCTUATION)
    return bool(wanted) and wanted in seen


def number_in(value: int | None, raw: str) -> bool:
    """The integer occurs in the raw text as a whole number (not inside a longer number)."""
    if value is None or value <= 0:
        return False
    return re.search(rf"(?<![\d.]){value}(?![\d])", raw or "") is not None


def parse_time(value: str | None) -> datetime | None:
    """An ISO date/datetime or epoch seconds, as an aware UTC datetime."""
    text = (value or "").strip().replace("Z", "+00:00")
    epoch = _EPOCH.match(text)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_day(value: str | None) -> date | None:
    parsed = parse_time(value)
    return parsed.date() if parsed else None


def livemode(seen_text: str) -> bool | None:
    """From the tool output itself: False when every record says test mode, None when none says."""
    flags = {m.group(1).lower() for m in _LIVEMODE.finditer(seen_text or "")}
    return None if not flags else "true" in flags


class Evidence:
    """One turn's verification corpus: tool output (fenced) plus the user's own message."""

    def __init__(self, raw: str):
        self.raw = raw or ""
        self.seen = normalise_text(self.raw)

    def has(self, value: str | None) -> bool:
        return appears_in(value, self.seen)

    def kept(self, value: str | None) -> str | None:
        return value if self.has(value) else None

    def amount(self, value: int | None) -> bool:
        return number_in(value, self.raw)

    def time(self, value: str | None) -> str | None:
        return value if parse_time(value) else None


def charge(item: Charge | None, ev: Evidence) -> Charge | None:
    if item is None or not ev.has(item.id) or not ev.amount(item.amount_minor) or len(item.currency) != 3:
        return None
    if parse_time(item.created_at) is None:
        return None
    refunded = item.refunded_minor if item.refunded_minor == 0 or ev.amount(item.refunded_minor) else 0
    return item.model_copy(update={
        "refunded_minor": min(refunded, item.amount_minor), "payment_intent": ev.kept(item.payment_intent),
        "description": ev.kept(item.description), "customer_id": ev.kept(item.customer_id),
        "invoice_id": ev.kept(item.invoice_id), "card_brand": ev.kept(item.card_brand),
        "card_country": ev.kept(item.card_country), "link": ev.kept(item.link)})


def recent_charges(items: tuple[Charge, ...], ev: Evidence, exclude: str | None) -> tuple[Charge, ...]:
    kept = (charge(c, ev) for c in items if c.id != exclude)
    return tuple({c.id: c for c in kept if c is not None}.values())


def candidates(items: tuple[Candidate, ...], ev: Evidence) -> tuple[Candidate, ...]:
    good = [c.model_copy(update={"name": ev.kept(c.name)}) for c in items
            if EMAIL.fullmatch(c.email) and ev.has(c.email)]
    return tuple({c.email.casefold(): c for c in good}.values())[:MAX_CANDIDATES]


def customer_file(item: CustomerFile, ev: Evidence) -> CustomerFile:
    customer = item.customer
    if customer is not None and not (ev.has(customer.id) and ev.has(customer.email) and EMAIL.fullmatch(customer.email)):
        customer = None
    if customer is not None:
        customer = Customer(id=customer.id, email=customer.email, name=ev.kept(customer.name))
    subs = tuple(s.model_copy(update={
        "plan_name": ev.kept(s.plan_name), "interval": s.interval if s.interval in _INTERVALS else None,
        "amount_minor": s.amount_minor if ev.amount(s.amount_minor) else None, "created_at": ev.time(s.created_at),
        "current_period_start": ev.time(s.current_period_start), "current_period_end": ev.time(s.current_period_end),
        "canceled_at": ev.time(s.canceled_at), "link": ev.kept(s.link)}) for s in item.subscriptions if ev.has(s.id))
    invoices = tuple(i.model_copy(update={
        "number": ev.kept(i.number), "paid_at": ev.time(i.paid_at),
        "amount_minor": i.amount_minor if ev.amount(i.amount_minor) else None, "link": ev.kept(i.link)})
        for i in item.invoices if ev.has(i.id))
    refunds = tuple(r.model_copy(update={
        "amount_minor": r.amount_minor if ev.amount(r.amount_minor) else None, "at": ev.time(r.at),
        "reason": ev.kept(r.reason), "charge_id": ev.kept(r.charge_id)}) for r in item.prior_refunds if ev.has(r.id))
    disputes = tuple(d.model_copy(update={"at": ev.time(d.at), "charge_id": ev.kept(d.charge_id)})
                     for d in item.prior_disputes if ev.has(d.id))
    return CustomerFile(customer=customer, subscriptions=_unique(subs), invoices=_unique(invoices),
                        prior_refunds=_unique(refunds), prior_disputes=_unique(disputes))


def dispute(item: Dispute, ev: Evidence) -> Dispute | None:
    if not ev.has(item.id) or not ev.amount(item.amount_minor) or len(item.currency) != 3:
        return None
    return item.model_copy(update={"charge_id": ev.kept(item.charge_id), "evidence_due_by": ev.time(item.evidence_due_by),
                                   "network_reason_code": ev.kept(item.network_reason_code), "link": ev.kept(item.link)})


def disputes(items: tuple[Dispute, ...], ev: Evidence) -> tuple[Dispute, ...]:
    return _unique(tuple(d for d in (dispute(i, ev) for i in items) if d is not None))


def messages(items: tuple[Msg, ...], ev: Evidence) -> tuple[tuple[Msg, ...], int]:
    """(verified messages newest first, how many were dropped)."""
    kept = []
    for msg in items:
        link, thread = ev.kept(msg.link), ev.kept(msg.thread_ref)
        if parse_time(msg.at) is None or not (link or thread):
            continue
        kept.append(msg.model_copy(update={"link": link, "thread_ref": thread, "quote": ev.kept(msg.quote),
                                           "subject": msg.subject if ev.has(msg.subject) else ""}))
    ordered = sorted(kept, key=lambda m: parse_time(m.at), reverse=True)[:MAX_MESSAGES]
    return tuple(ordered), len(items) - len(ordered)


def _unique(items: tuple) -> tuple:
    return tuple({i.id: i for i in items}.values())


__all__ = ["Charge", "Customer", "CustomerFile", "Dispute", "Evidence", "Invoice", "Msg", "PriorDispute",
           "PriorRefund", "Subscription", "appears_in", "candidates", "charge", "customer_file", "dispute",
           "disputes", "livemode", "messages", "normalise_text", "number_in", "parse_day", "parse_time",
           "recent_charges"]
