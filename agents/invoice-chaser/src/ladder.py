"""Pure: days overdue, the escalation ladder, cooldown, amount escalation, priority, ranking and totals.
Every threshold lives here. The model never sees a number it has to compute."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from checks import parse_day, reminders_after_due
from invoices import ZERO_DECIMAL, Entry, Invoice

FIRM_FROM_DAYS = 7
FINAL_FROM_DAYS = 14
ESCALATE_FROM_DAYS = 30
COOLDOWN_DAYS = 3
RESPONSE_DEADLINE_DAYS = 5
REPLIED_LEVEL_CAP = 2
LARGE_AMOUNT_MINOR = 500_000          # 5,000.00 in a two-decimal currency
LARGE_AMOUNT_FROM_DAYS = 14
UNCHECKED_NEEDS_HUMAN_DAYS = 14
DAYS_CAP = 45
MAX_LISTED = 40
AMOUNT_WEIGHTS = ((100, 0), (1_000, 10), (10_000, 20))  # (below this many major units, weight)
TOP_AMOUNT_WEIGHT = 30

LEVEL_NAMES = {1: "friendly_reminder", 2: "firm_reminder", 3: "final_notice", 4: "escalate_human"}
LEVEL_LABELS = {1: "friendly reminder", 2: "firm reminder", 3: "final notice", 4: "escalate to a human"}
AUTO_CHARGE_ACTION = "auto-charge, see Failed Payment Rescuer"


@dataclass(frozen=True)
class LadderState:
    level: int
    name: str
    cooling_down: bool
    customer_replied: bool
    reminders_sent: int
    days_overdue: int
    amount_escalation: bool
    cooldown_left: int = 0            # days until the cooldown ends

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]


@dataclass(frozen=True)
class Ranked:
    entries: tuple[Entry, ...]
    capped: int = 0


def days_overdue(due: date, today: date) -> int:
    return (today - due).days


def base_level(days: int) -> int:
    if days >= ESCALATE_FROM_DAYS:
        return 4
    if days >= FINAL_FROM_DAYS:
        return 3
    if days >= FIRM_FROM_DAYS:
        return 2
    return 1


def assess(entry: Entry, today: date) -> LadderState:
    invoice = entry.invoice
    days = days_overdue(invoice.due_day, today)
    sent = reminders_after_due(entry)
    level = max(base_level(days), min(4, len(sent) + 1))
    large = invoice.amount_due_minor >= LARGE_AMOUNT_MINOR and days >= LARGE_AMOUNT_FROM_DAYS
    if large:
        level = max(level, 3)
    replied = entry.reply is not None
    if replied:  # the conversation is live: never above a firm reminder (decision: the cap wins)
        level = min(level, REPLIED_LEVEL_CAP)
    last = max((d for d in (parse_day(r.sent_at) for r in entry.reminders) if d), default=None)
    since = (today - last).days if last else None
    cooling = since is not None and since < COOLDOWN_DAYS
    return LadderState(level=level, name=LEVEL_NAMES[level], cooling_down=cooling, customer_replied=replied,
                       reminders_sent=len(sent), days_overdue=days, amount_escalation=large,
                       cooldown_left=COOLDOWN_DAYS - since if cooling else 0)


def needs_human(entry: Entry, state: LadderState) -> bool:
    unchecked = not entry.reminders_checked and state.days_overdue >= UNCHECKED_NEEDS_HUMAN_DAYS
    return state.level == 4 or state.amount_escalation or state.customer_replied or unchecked


def response_deadline(today: date) -> date:
    return today + timedelta(days=RESPONSE_DEADLINE_DAYS)


def major_units(minor: int, currency: str) -> float:
    return float(minor) if currency.casefold() in ZERO_DECIMAL else minor / 100


def money(minor: int, currency: str) -> str:
    """4000.00 USD → "4,000.00 USD"; zero-decimal currencies without decimals."""
    if currency.casefold() in ZERO_DECIMAL:
        return f"{minor:,} {currency.upper()}"
    return f"{minor / 100:,.2f} {currency.upper()}"


def amount_weight(invoice: Invoice) -> int:
    value = major_units(invoice.amount_due_minor, invoice.currency)
    return next((w for limit, w in AMOUNT_WEIGHTS if value < limit), TOP_AMOUNT_WEIGHT)


def priority_score(entry: Entry, today: date) -> int:
    days = days_overdue(entry.invoice.due_day, today)
    return min(days, DAYS_CAP) * 2 + amount_weight(entry.invoice)


def rank(entries: Sequence[Entry], today: date) -> Ranked:
    """Score desc, then amount desc, then due date asc; numbered #1..; capped at MAX_LISTED."""
    ordered = sorted(entries, key=lambda e: (-priority_score(e, today),
                                             -major_units(e.invoice.amount_due_minor, e.invoice.currency),
                                             e.invoice.due))
    shown = ordered[:MAX_LISTED]
    numbered = tuple(replace(e, id=str(n)) for n, e in enumerate(shown, start=1))
    return Ranked(numbered, len(ordered) - len(shown))


def totals(entries: Sequence[Entry]) -> dict[str, int]:
    """Outstanding per currency, in minor units; never converted."""
    sums: dict[str, int] = {}
    for e in entries:
        sums = {**sums, e.invoice.currency: sums.get(e.invoice.currency, 0) + e.invoice.amount_due_minor}
    return sums


def totals_text(sums: Mapping[str, int]) -> str:
    return ", ".join(money(v, c) for c, v in sorted(sums.items())) or "nothing"


def next_action(entry: Entry, state: LadderState) -> str:
    if entry.invoice.auto_charge:
        return AUTO_CHARGE_ACTION
    action = "internal escalation note (call them)" if state.level == 4 else state.label
    if state.cooling_down:
        return f"{action} (wait {state.cooldown_left}d)"
    return action
