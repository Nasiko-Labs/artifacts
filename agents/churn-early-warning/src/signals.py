"""Pure: the churn signals, each decided in code from checked data, with a weight and verbatim evidence.

Billing (from subscriptions, change events and failed payments), email (a verbatim complaint, silence,
recent contact) and support desk (an open high-priority ticket, ticket volume). A source that could not
be read is recorded under `not_checked` on the account, never scored as "no signal"."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from fractions import Fraction

import mrr
from records import Change, Failure, Item, MailRecord, Sub, Ticket, parse_day

DOWNGRADE_MAJOR = Fraction(30, 100)
RENEWAL_SOON_DAYS = 7
TRIAL_DAYS = 7
COMPLAINT_DAYS = 30
SILENT_DAYS = 45
SENT_RECENT_DAYS = 90
RECENT_CONTACT_DAYS = 7
TICKET_WINDOW_DAYS = 30
TICKET_VOLUME = 3
FAILED_ATTEMPTS = 2
OPEN_TICKET = frozenset({"open", "new", "pending", "hold", "on-hold", "on_hold"})
HIGH_PRIORITY = frozenset({"high", "urgent"})
LIVE = frozenset({"active", "trialing", "past_due", "unpaid", "incomplete"})
EMAIL, DESK = "email", "support desk"

WEIGHTS = {"canceled": 10, "cancel_scheduled": 9, "downgrade": 6, "seat_reduction": 5, "past_due": 6,
           "payment_failed_repeatedly": 7, "renewal_near": 3, "trial_ending": 3, "complaint": 8, "silent": 4,
           "recent_contact": -2, "open_high_priority_ticket": 6, "ticket_volume": 4}
LABELS = {"canceled": "cancelled", "cancel_scheduled": "cancel scheduled", "downgrade": "downgrade",
          "seat_reduction": "seat reduction", "past_due": "past due",
          "payment_failed_repeatedly": "payments failing", "renewal_near": "renewal near",
          "trial_ending": "trial ending", "complaint": "complaint", "silent": "gone quiet",
          "recent_contact": "recent contact", "open_high_priority_ticket": "urgent ticket",
          "ticket_volume": "many tickets"}


@dataclass(frozen=True)
class Signal:
    kind: str
    weight: int
    evidence: str
    link: str = ""


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    email: str
    currency: str
    mrr_minor: int
    link: str = ""
    signals: tuple[Signal, ...] = ()
    not_checked: tuple[str, ...] = ()
    effective_dates: tuple[str, ...] = ()   # ISO days of a coming renewal or cancellation
    excluded_items: int = 0
    reason: str = ""                         # cancellation reason or comment, verbatim, when present
    reply_thread: str = ""
    reply_link: str = ""
    complaint_subject: str = ""

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(s.kind for s in self.signals)


def signal(kind: str, evidence: str, link: str = "", weight: int | None = None) -> Signal:
    return Signal(kind, WEIGHTS[kind] if weight is None else weight, evidence, link)


def strongest(signals: Iterable[Signal]) -> tuple[Signal, ...]:
    """One signal per kind: the heaviest (first seen on a tie), in first-seen order."""
    best: dict[str, Signal] = {}
    for s in signals:
        if s.kind not in best or s.weight > best[s.kind].weight:
            best = {**best, s.kind: s}
    return tuple(best.values())


def _days(day: date | None, today: date) -> int | None:
    return None if day is None else (day - today).days


def _in_past(day: date | None, today: date, window: int) -> bool:
    return day is not None and today - timedelta(days=window) <= day <= today


# --- billing --------------------------------------------------------------------------------------

def sub_signals(sub: Sub, today: date, lookahead: int, lookback: int) -> tuple[Signal, ...]:
    out: list[Signal] = []
    canceled = parse_day(sub.canceled_at)
    if sub.status == "canceled":
        if _in_past(canceled, today, lookback):
            out.append(signal("canceled", f"subscription {sub.id} cancelled on {canceled.isoformat()}", sub.link))
        return tuple(out)
    ends = cancel_effective(sub)
    if ends is not None and ends >= today:
        out.append(signal("cancel_scheduled", f"subscription {sub.id} set to cancel on {ends.isoformat()}", sub.link))
    if sub.status == "past_due":
        out.append(signal("past_due", f"subscription {sub.id} is past_due", sub.link))
    renews = parse_day(sub.current_period_end)
    days = _days(renews, today)
    if ends is None and sub.status in ("active", "trialing") and days is not None and 0 <= days <= lookahead:
        weight = 4 if days <= RENEWAL_SOON_DAYS else 3
        out.append(signal("renewal_near", f"renews {renews.isoformat()} (in {days} days)", sub.link, weight))
    trial = parse_day(sub.trial_end)
    trial_days = _days(trial, today)
    if sub.status == "trialing" and trial_days is not None and 0 <= trial_days <= TRIAL_DAYS:
        out.append(signal("trial_ending", f"trial ends {trial.isoformat()} (in {trial_days} days)", sub.link))
    return tuple(out)


def cancel_effective(sub: Sub) -> date | None:
    if sub.cancel_at:
        return parse_day(sub.cancel_at)
    return parse_day(sub.current_period_end) if sub.cancel_at_period_end else None


def change_signal(change: Change, sub: Sub, today: date, lookback: int) -> Signal | None:
    """A lower monthly amount at the same prices is a seat reduction; any other lower amount a downgrade."""
    at = parse_day(change.at)
    if change.type != "updated" or not change.previous_items or not _in_past(at, today, lookback):
        return None
    before = mrr.items_monthly(change.previous_items, sub.interval, sub.interval_count, sub.percent_off)
    after = mrr.items_monthly(change.items, sub.interval, sub.interval_count, sub.percent_off)
    if before.excluded or after.excluded or before.minor <= 0 or after.minor >= before.minor:
        return None
    if _prices(change.previous_items) == _prices(change.items) and _seats(change.items) < _seats(change.previous_items):
        evidence = (f"seats {_seats(change.previous_items)} → {_seats(change.items)} on subscription {sub.id} "
                    f"({at.isoformat()})")
        return signal("seat_reduction", evidence, sub.link)
    cut = Fraction(before.minor - after.minor, before.minor)
    weight = 7 if cut >= DOWNGRADE_MAJOR else 6
    evidence = (f"monthly {mrr.money(before.minor, sub.currency)} → {mrr.money(after.minor, sub.currency)} "
                f"(-{mrr.half_up(cut * 100)}%) on {at.isoformat()}")
    return signal("downgrade", evidence, sub.link, weight)


def _prices(items: Sequence[Item]) -> tuple:
    return tuple(sorted(i.unit_amount_minor or 0 for i in items))


def _seats(items: Sequence[Item]) -> int:
    return sum(i.quantity for i in items)


def failure_signals(failures: Sequence[Failure], today: date, lookback: int) -> tuple[Signal, ...]:
    counts = Counter(f.invoice_id for f in failures if f.invoice_id and _in_past(parse_day(f.at), today, lookback))
    return tuple(signal("payment_failed_repeatedly", f"{n} failed payment attempts on invoice {invoice}")
                 for invoice, n in counts.items() if n >= FAILED_ATTEMPTS)


def billing_accounts(subs: Sequence[Sub], changes: Sequence[Change], failures: Sequence[Failure], today: date,
                     lookahead: int, lookback: int) -> tuple[Account, ...]:
    by_customer: dict[str, list[Sub]] = {}
    for sub in subs:
        by_customer = {**by_customer, sub.customer_id: [*by_customer.get(sub.customer_id, []), sub]}
    by_sub = {s.id: s for s in subs}
    return tuple(_account(cid, own, [c for c in changes if c.subscription_id in {s.id for s in own}], by_sub,
                          [f for f in failures if f.customer_id == cid], today, lookahead, lookback)
                 for cid, own in by_customer.items())


def _account(cid: str, subs: Sequence[Sub], changes: Sequence[Change], by_sub: Mapping[str, Sub],
             failures: Sequence[Failure], today: date, lookahead: int, lookback: int) -> Account:
    live = [s for s in subs if s.status != "canceled"] or list(subs)
    monthly = {s.id: mrr.sub_monthly(s) for s in subs}
    currency = max(live, key=lambda s: monthly[s.id].minor).currency
    found = [*(x for s in subs for x in sub_signals(s, today, lookahead, lookback)),
             *(x for c in changes if (x := change_signal(c, by_sub[c.subscription_id], today, lookback))),
             *failure_signals(failures, today, lookback)]
    dates = {d for s in live if s.status != "canceled"
             for d in (cancel_effective(s), parse_day(s.current_period_end)) if d is not None and d >= today}
    reason = next((x for s in subs for x in (s.cancellation_reason, s.cancellation_comment) if x), "")
    return Account(
        id=cid, name=next((s.customer_name for s in subs if s.customer_name), "")
        or next((s.customer_email for s in subs if s.customer_email), "") or cid,
        email=next((s.customer_email for s in subs if s.customer_email), ""), currency=currency,
        mrr_minor=sum(monthly[s.id].minor for s in live if s.currency == currency),
        link=next((s.link for s in subs if s.link), ""), signals=strongest(found),
        effective_dates=tuple(sorted(d.isoformat() for d in dates)),
        excluded_items=sum(m.excluded for m in monthly.values()), reason=reason)


# --- email and support desk -----------------------------------------------------------------------

def with_mail(account: Account, record: MailRecord | None, today: date) -> Account:
    if record is None:
        return replace(account, not_checked=_add(account.not_checked, EMAIL))
    recent = [i for i in record.inbound if _in_past(parse_day(i.at), today, COMPLAINT_DAYS)]
    complaint = next((i for i in sorted(recent, key=lambda i: i.at, reverse=True) if i.quote), None)
    found: list[Signal] = []
    if complaint is not None:
        found.append(signal("complaint", complaint.quote, complaint.link))
    last_in = _latest([*(parse_day(i.at) for i in record.inbound), parse_day(record.last_inbound_at)])
    if complaint is None and _in_past(last_in, today, RECENT_CONTACT_DAYS):
        found.append(signal("recent_contact", f"they last emailed you on {last_in.isoformat()}"))
    last_out = parse_day(record.last_sent_at)
    quiet = last_in is None or (today - last_in).days >= SILENT_DAYS
    if quiet and _in_past(last_out, today, SENT_RECENT_DAYS):
        gap = (f"no email from them for {(today - last_in).days} days" if last_in
               else "no email from them on record")
        found.append(signal("silent", f"{gap}; you last wrote on {last_out.isoformat()}"))
    latest = max(record.inbound, key=lambda i: parse_day(i.at) or date.min, default=None)
    return replace(account, signals=strongest((*account.signals, *found)),
                   reply_thread=latest.thread_id if latest else "", reply_link=latest.link if latest else "",
                   complaint_subject=complaint.subject if complaint else "")


def with_desk(account: Account, tickets: Sequence[Ticket] | None, today: date) -> Account:
    if tickets is None:
        return replace(account, not_checked=_add(account.not_checked, DESK))
    found: list[Signal] = []
    urgent = next((t for t in tickets if t.status in OPEN_TICKET and t.priority in HIGH_PRIORITY), None)
    if urgent is not None:
        found.append(signal("open_high_priority_ticket",
                            f"ticket {urgent.id} ({urgent.priority}, {urgent.status}): {urgent.subject}", urgent.link))
    recent = [t for t in tickets if _in_past(parse_day(t.created_at), today, TICKET_WINDOW_DAYS)]
    if len(recent) >= TICKET_VOLUME:
        found.append(signal("ticket_volume", f"{len(recent)} tickets in the last {TICKET_WINDOW_DAYS} days"))
    return replace(account, signals=strongest((*account.signals, *found)))


def _latest(days: Iterable[date | None]) -> date | None:
    known = [d for d in days if d is not None]
    return max(known) if known else None


def _add(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)
