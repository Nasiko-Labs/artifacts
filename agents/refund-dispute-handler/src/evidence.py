"""Pure: evidence. For a refund request, the items the recommendation rests on (each with its source id).
For a dispute, the deadline maths, the checklist by dispute reason (an item is available only when the data
exists), the contest / accept / needs-human recommendation, and the packet text the user pastes into their
payments dashboard. Nothing is claimed without a source id; delivery proof and usage logs are never
available here."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from case import Charge, CustomerFile, Dispute, Msg, Policy, money
from checks import parse_day, parse_time
from facts import Facts, days_between
from policy import Recommendation

URGENT_DAYS = 3
MIN_CONTEST_ITEMS = 2
ADD_YOURS = "not available here: add yours"
_REASONS = ("fraudulent", "product_not_received", "subscription_canceled", "duplicate", "credit_not_processed",
            "general")
_ITEMS_BY_REASON = {
    "fraudulent": ("customer", "country", "prior_payments", "correspondence", "invoices", "usage"),
    "product_not_received": ("customer", "delivery", "correspondence", "invoices", "usage"),
    "subscription_canceled": ("customer", "subscription", "cancellation", "policy", "correspondence", "invoices"),
    "duplicate": ("customer", "invoices", "refunds", "policy"),
    "credit_not_processed": ("customer", "policy", "refunds", "correspondence"),
    "general": ("customer", "invoices", "prior_payments", "correspondence", "policy"),
}


@dataclass(frozen=True)
class EvidenceItem:
    name: str
    available: bool
    value: str = ""
    source_id: str | None = None
    link: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> EvidenceItem:
        return EvidenceItem(**{k: v for k, v in data.items() if k in EvidenceItem.__dataclass_fields__})


@dataclass(frozen=True)
class Deadline:
    due: str | None
    days_left: int | None
    urgent: bool
    closed: bool


# --- refund requests ----------------------------------------------------------------------------------------

def refund_items(charge: Charge | None, file: CustomerFile, recent: tuple[Charge, ...], messages: tuple[Msg, ...],
                 facts: Facts) -> tuple[EvidenceItem, ...]:
    source = "pasted" if facts.pasted else None
    items: list[EvidenceItem] = []
    if charge is not None:
        refunded = f", {money(charge.refunded_minor, charge.currency)} already refunded" if charge.refunded_minor else ""
        items.append(EvidenceItem("Charge", True, f"{money(charge.amount_minor, charge.currency)} on "
                                  f"{facts.charge_date}{refunded}", source or charge.id, charge.link))
    other = next((c for c in recent if c.id == facts.duplicate_of), None)
    if other is not None:
        items.append(EvidenceItem("Duplicate charge", True, f"{money(other.amount_minor, other.currency)} on "
                                  f"{parse_day(other.created_at)}", other.id, other.link))
    items.extend(EvidenceItem("Prior refund", True, f"{money(r.amount_minor, facts.currency)} on {parse_day(r.at)}",
                              r.id) for r in file.prior_refunds[:3])
    quote = next((m for m in messages if m.direction == "in" and m.quote), None)
    if quote is not None:
        items.append(EvidenceItem("Customer email", True, f"{parse_day(quote.at)}: \"{quote.quote}\"",
                                  quote.thread_ref or quote.link, quote.link))
    return tuple(items)


# --- disputes -----------------------------------------------------------------------------------------------

def deadline(dispute: Dispute, now: datetime) -> Deadline:
    due = parse_time(dispute.evidence_due_by)
    if due is None:
        return Deadline(None, None, False, False)
    if due < now:
        return Deadline(due.date().isoformat(), -days_between(due, now), False, True)
    left = days_between(now, due)
    return Deadline(due.date().isoformat(), left, left <= URGENT_DAYS, False)


def reason_of(dispute: Dispute) -> str:
    return dispute.reason if dispute.reason in _REASONS else "general"


def checklist(dispute: Dispute, charge: Charge | None, file: CustomerFile, messages: tuple[Msg, ...],
              policy: Policy) -> tuple[EvidenceItem, ...]:
    builders = {
        "customer": lambda: _customer(file),
        "country": lambda: _country(charge),
        "prior_payments": lambda: _prior_payments(file, charge),
        "invoices": lambda: _invoices(file),
        "subscription": lambda: _subscription(file),
        "cancellation": lambda: _cancellation(file, charge),
        "correspondence": lambda: _correspondence(messages),
        "policy": lambda: _policy(policy),
        "refunds": lambda: _refunds(file, dispute),
        "delivery": lambda: EvidenceItem("Delivery proof", False, ADD_YOURS),
        "usage": lambda: EvidenceItem("Usage logs", False, ADD_YOURS),
    }
    return tuple(builders[key]() for key in _ITEMS_BY_REASON[reason_of(dispute)])


def recommend_dispute(dispute: Dispute, charge: Charge | None, file: CustomerFile, messages: tuple[Msg, ...],
                      items: tuple[EvidenceItem, ...], when: Deadline) -> Recommendation:
    reason = reason_of(dispute)
    flags = (f"URGENT: evidence due in {when.days_left} days",) if when.urgent else ()
    flags = (*flags, "evidence window closed") if when.closed else flags
    refunded = next((r for r in file.prior_refunds if r.amount_minor == dispute.amount_minor
                     and (r.charge_id in (None, dispute.charge_id))), None)
    if refunded is not None:
        return Recommendation("accept_dispute", reasons=(
            f"a refund {refunded.id} for the same amount already exists: the customer has the money back",),
            flags=flags, rule="already_refunded", needs_human=when.urgent, charge_id=dispute.charge_id)
    receipt = bool(_prior_payments(file, charge).available or any(m.direction == "in" for m in messages))
    if reason == "fraudulent" and not receipt:
        return Recommendation("accept_dispute", reasons=(
            ("reason fraudulent and nothing shows the customer received the service (no prior payments, no "
            "correspondence): low win odds"),), flags=flags, rule="fraud_no_receipt", needs_human=when.urgent,
            charge_id=dispute.charge_id)
    available = [i.name for i in items if i.available]
    known = file.customer is not None
    if charge is not None and charge.paid and known and len(available) >= MIN_CONTEST_ITEMS:
        return Recommendation("contest", reasons=(
            (f"the charge was paid, the customer is known, and {len(available)} evidence items are available "
            f"({', '.join(available)})"),), flags=flags, rule="contest", needs_human=when.urgent,
            charge_id=dispute.charge_id)
    missing = "the charge could not be read" if charge is None else (
        "the customer could not be read" if not known else f"only {len(available)} evidence item(s) available")
    return Recommendation("needs_human", reasons=(f"not enough to decide: {missing}",), flags=flags,
                          rule="dispute_insufficient", needs_human=True, charge_id=dispute.charge_id)


def packet(dispute: Dispute, items: tuple[EvidenceItem, ...], when: Deadline) -> str:
    """Ordered sections, each with its facts and source ids, as plain text to paste."""
    head = [f"Dispute {dispute.id} ({reason_of(dispute)}), {money(dispute.amount_minor, dispute.currency)}",
            f"Charge: {dispute.charge_id or 'unknown'}"]
    if when.due:
        head.append(f"Evidence due by: {when.due}")
    sections = [f"{n}. {item.name}: {item.value} [source: {item.source_id}]" if item.available
                else f"{n}. {item.name}: {item.value or ADD_YOURS}" for n, item in enumerate(items, start=1)]
    return "\n".join([*head, "", *sections])


def _customer(file: CustomerFile) -> EvidenceItem:
    c = file.customer
    if c is None:
        return EvidenceItem("Customer email and name", False, "customer not read")
    return EvidenceItem("Customer email and name", True, f"{c.name or 'name unknown'} <{c.email}>", c.id)


def _country(charge: Charge | None) -> EvidenceItem:
    if charge is None or not charge.card_country:
        return EvidenceItem("Billing country vs card country", False, "card country not in the charge")
    return EvidenceItem("Billing country vs card country", True, f"card country {charge.card_country} (compare with "
                        "the billing address)", charge.id, charge.link)


def _prior_payments(file: CustomerFile, charge: Charge | None) -> EvidenceItem:
    own = {charge.invoice_id} if charge and charge.invoice_id else set()
    paid = sorted((d, i.id) for i in file.invoices if i.id not in own and (d := parse_day(i.paid_at)) is not None)
    if not paid:
        return EvidenceItem("Prior successful payments", False, "none found")
    return EvidenceItem("Prior successful payments", True, f"{len(paid)} paid invoice(s), first on {paid[0][0]}",
                        ", ".join(i for _, i in paid[:5]))


def _invoices(file: CustomerFile) -> EvidenceItem:
    rows = [f"{i.number or i.id} ({parse_day(i.paid_at) or 'unpaid'})" for i in file.invoices[:5]]
    if not rows:
        return EvidenceItem("Invoices", False, "none found")
    return EvidenceItem("Invoices", True, "; ".join(rows), ", ".join(i.id for i in file.invoices[:5]),
                        file.invoices[0].link)


def _subscription(file: CustomerFile) -> EvidenceItem:
    sub = file.subscriptions[0] if file.subscriptions else None
    if sub is None:
        return EvidenceItem("Subscription", False, "none found")
    return EvidenceItem("Subscription", True, f"{sub.plan_name or 'plan'} started {parse_day(sub.created_at) or 'unknown'},"
                        f" status {sub.status or 'unknown'}", sub.id, sub.link)


def _cancellation(file: CustomerFile, charge: Charge | None) -> EvidenceItem:
    sub = next((s for s in file.subscriptions if s.canceled_at), None)
    if sub is None:
        return EvidenceItem("Cancellation date", False, "no cancellation found")
    canceled = parse_time(sub.canceled_at)
    charged = parse_time(charge.created_at) if charge else None
    order = ("" if charged is None else
             " (the charge came after it)" if charged > canceled else " (the charge came before it)")
    return EvidenceItem("Cancellation date", True, f"{canceled.date()}{order}", sub.id, sub.link)


def _correspondence(messages: tuple[Msg, ...]) -> EvidenceItem:
    quoted = [m for m in messages if m.quote]
    if not quoted:
        return EvidenceItem("Email correspondence", False, "no verified quotes")
    lines = "; ".join(f"{parse_day(m.at)} ({'customer' if m.direction == 'in' else 'you'}): \"{m.quote}\""
                      for m in quoted[:3])
    first = quoted[0]
    return EvidenceItem("Email correspondence", True, lines, first.thread_ref or first.link, first.link)


def _policy(policy: Policy) -> EvidenceItem:
    if not policy.text:
        return EvidenceItem("Refund policy", False, "add your published refund policy text or link")
    return EvidenceItem("Refund policy", True, policy.text[:300], "organization policy")


def _refunds(file: CustomerFile, dispute: Dispute) -> EvidenceItem:
    rows = [r for r in file.prior_refunds if r.charge_id in (None, dispute.charge_id)]
    if not rows:
        return EvidenceItem("Refunds issued", False, "none found")
    return EvidenceItem("Refunds issued", True, "; ".join(f"{r.id} on {parse_day(r.at)}" for r in rows[:3]),
                        ", ".join(r.id for r in rows[:3]))
