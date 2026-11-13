"""Pure: category (when the signals are clear), priority, escalation risk, SLA and suggested owner, computed in
code from the checked signals and the pasted fields. One policy, kept in the tables below so it can be retuned in
one place. The model never assigns any of these; its category stands only when no signal decides it."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace

from inputs import Config
from model import Ticket

PRIORITIES = ("P1", "P2", "P3", "P4")
SLA_CRITICAL = ("enterprise", "business")
REPEAT_BUMP_CONTACTS = 3
OLD_OPEN_HOURS = 72
HIGH_RISK, MEDIUM_RISK = 5, 2
P3_CATEGORIES = ("bug", "billing", "account_access", "data_issue", "cancellation", "outage", "security")
BROKEN_CATEGORIES = ("bug", "billing", "account_access", "data_issue", "outage")  # something stopped working
_SOON = re.compile(r"\b(?:today|tonight|tomorrow|eod|end of (?:the )?day|asap|this (?:morning|afternoon|evening)|"
                   r"within (?:the )?(?:hour|\d+ ?h(?:ours?)?))\b", re.IGNORECASE)


def _crit(t: Ticket) -> bool:
    return t.tier in SLA_CRITICAL


def _deadline_soon(t: Ticket) -> bool:
    """A stated deadline that falls within about a day (read from its quote; no date arithmetic)."""
    return any(s.name == "deadline" and _SOON.search(s.quote) for s in t.signals)


# (priority, rule text, condition); the first matching row wins.
PRIORITY_TABLE: tuple[tuple[str, str, Callable[[Ticket], bool]], ...] = (
    ("P1", "service_down with blocked_work", lambda t: t.has("service_down") and t.has("blocked_work")),
    ("P1", "service_down for all or most users", lambda t: t.has("service_down") and t.has("widespread")),
    ("P1", "service_down on an enterprise/business tier", lambda t: t.has("service_down") and _crit(t)),
    ("P1", "data_loss", lambda t: t.has("data_loss")),
    ("P1", "security_concern", lambda t: t.has("security_concern")),
    ("P1", "legal_threat", lambda t: t.has("legal_threat")),
    ("P2", "service_down", lambda t: t.has("service_down")),
    ("P2", "blocked_work on an enterprise/business tier", lambda t: t.has("blocked_work") and _crit(t)),
    ("P2", "blocked_work with a deadline", lambda t: t.has("blocked_work") and t.has("deadline")),
    ("P2", "a broken function with a deadline", lambda t: t.category in BROKEN_CATEGORIES and t.has("deadline")),
    ("P2", "blocked_work for all or most users", lambda t: t.has("blocked_work") and t.has("widespread")),
    ("P2", "churn_threat on an enterprise/business tier", lambda t: t.has("churn_threat") and _crit(t)),
    ("P2", "exec_involved on an enterprise tier", lambda t: t.has("exec_involved") and t.tier == "enterprise"),
    ("P2", "money_at_stake", lambda t: t.has("money_at_stake")),
    ("P3", "category needs a fix", lambda t: t.category in P3_CATEGORIES),
    ("P3", "repeat_contact", lambda t: t.has("repeat_contact")),
)
FALLBACK = ("P4", "no escalation signal")

# (label, points, condition), summed for escalation risk.
RISK_TABLE: tuple[tuple[str, int, Callable[[Ticket], bool]], ...] = (
    ("service_down", 2, lambda t: t.has("service_down")),
    ("widespread", 2, lambda t: t.has("widespread")),
    ("data_loss", 2, lambda t: t.has("data_loss")),
    ("security_concern", 2, lambda t: t.has("security_concern")),
    ("deadline", 2, lambda t: t.has("deadline")),
    ("deadline within a day", 1, _deadline_soon),
    ("enterprise", 2, lambda t: t.tier == "enterprise"),
    ("business", 1, lambda t: t.tier == "business"),
    ("churn_threat", 2, lambda t: t.has("churn_threat")),
    ("legal_threat", 3, lambda t: t.has("legal_threat")),
    ("exec_involved", 2, lambda t: t.has("exec_involved")),
    ("repeat_contact", 1, lambda t: t.has("repeat_contact")),
    (f"{REPEAT_BUMP_CONTACTS}+ contacts", 1, lambda t: t.contacts >= REPEAT_BUMP_CONTACTS),
    ("angry_tone", 1, lambda t: t.has("angry_tone")),
    ("blocked_work", 1, lambda t: t.has("blocked_work")),
    ("money_at_stake", 1, lambda t: t.has("money_at_stake")),
    (f"open > {OLD_OPEN_HOURS}h", 1, lambda t: t.is_open and t.age_hours is not None and t.age_hours > OLD_OPEN_HOURS),
    ("past SLA", 1, lambda t: t.past_sla),
)

_BILLING = re.compile(r"\b(?:billing|billed|invoices?|refunds?|charged|overcharg\w*|double[- ]charg\w*|"
                      r"payment (?:failed|declined))\b", re.IGNORECASE)

# (rule text, condition, category, owner); the first matching row overrides the model's category, because these
# signals decide who has to act. No row matches: the model's category stands, with DEFAULT_OWNERS.
CATEGORY_TABLE: tuple[tuple[str, Callable[[Ticket], bool], str, str], ...] = (
    ("security_concern", lambda t: t.has("security_concern"), "security", "Security team"),
    ("service_down", lambda t: t.has("service_down"), "outage", "Engineering on-call"),
    ("blocked_work for all or most users", lambda t: t.has("blocked_work") and t.has("widespread"), "outage",
     "Engineering on-call"),
    ("data_loss", lambda t: t.has("data_loss"), "data_issue", "Engineering"),
    ("billing words", lambda t: bool(_BILLING.search(t.text)), "billing", "Billing support"),
)

DEFAULT_OWNERS = {
    "outage": "Engineering on-call", "bug": "Support tier 2", "how_to": "Support tier 1",
    "billing": "Billing support", "account_access": "Support tier 1", "security": "Security team",
    "data_issue": "Support tier 2", "feature_request": "Product", "cancellation": "Account management",
    "other": "Support tier 1",
}


def priority(ticket: Ticket) -> tuple[str, str]:
    level, rule = next(((p, r) for p, r, cond in PRIORITY_TABLE if cond(ticket)), FALLBACK)
    if ticket.has("repeat_contact") and ticket.contacts >= REPEAT_BUMP_CONTACTS and level != "P1":
        level = PRIORITIES[PRIORITIES.index(level) - 1]
        rule += f"; raised one level for repeat_contact with {ticket.contacts} contacts"
    return level, rule


def past_sla(ticket: Ticket, level: str, sla_hours: dict[str, float]) -> bool:
    limit = sla_hours.get(level)
    return limit is not None and ticket.is_open and ticket.age_hours is not None and ticket.age_hours > limit


def risk(ticket: Ticket) -> tuple[str, tuple[tuple[str, int], ...]]:
    points = tuple((label, n) for label, n, cond in RISK_TABLE if cond(ticket))
    total = sum(n for _, n in points)
    level = "high" if total >= HIGH_RISK else "medium" if total >= MEDIUM_RISK else "low"
    return level, points


def categorise(ticket: Ticket) -> tuple[str, str, str | None]:
    """(category, rule text, owner) from the first CATEGORY_TABLE row that matches; else the model's category."""
    for rule, cond, category, team in CATEGORY_TABLE:
        if cond(ticket):
            return category, f"category {category} from {rule}", team
    return ticket.category, "", None


def owner(ticket: Ticket, routing: dict[str, str], derived: str | None = None) -> str:
    if ticket.category in routing:
        return routing[ticket.category]
    if derived:
        return derived
    if ticket.category == "account_access" and ticket.tier == "enterprise":
        return "Support tier 2"
    return DEFAULT_OWNERS.get(ticket.category, DEFAULT_OWNERS["other"])


def apply(ticket: Ticket, config: Config) -> Ticket:
    """A new ticket with category, priority, SLA flag, risk and owner filled in (reason is added by `reason.py`)."""
    category, category_rule, team = categorise(ticket)
    ticket = replace(ticket, category=category, category_rule=category_rule)
    level, rule = priority(ticket)
    flagged = replace(ticket, priority=level, priority_rule=rule,
                      past_sla=past_sla(ticket, level, dict(config.sla_hours)))
    risk_level, points = risk(flagged)
    return replace(flagged, escalation_risk=risk_level, risk_points=points,
                   suggested_owner=owner(flagged, dict(config.routing), team))


def sort_key(ticket: Ticket, by: str = "priority") -> tuple:
    risk_rank = {"high": 0, "medium": 1, "low": 2}[ticket.escalation_risk]
    age = -(ticket.age_hours if ticket.age_hours is not None else -1.0)
    prio = PRIORITIES.index(ticket.priority)
    if by == "risk":
        return (risk_rank, prio, age)
    if by == "age":
        return (age, prio, risk_rank)
    return (prio, risk_rank, age)
