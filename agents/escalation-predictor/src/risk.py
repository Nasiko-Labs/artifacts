"""Pure: the escalation-risk points table, levels and ranking. The model never scores anything; it only
labels sentiment, and that label counts only with a verified verbatim frustration quote."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from facts import TicketFacts
from records import RiskFactor, Sentiment, Ticket

TIERS = ("enterprise", "business", "pro")          # best first
TIER_POINTS = {"enterprise": 3, "business": 2, "pro": 1}
PRIORITY_POINTS = {"urgent": 3, "critical": 3, "high": 2, "normal": 1, "medium": 1}
AGE_STEPS = ((14, 3), (7, 2), (3, 1))               # (days at least, points)
UNANSWERED_STEPS = ((24, 2), (8, 1))                # (hours more than, points)
SENTIMENT_POINTS = ESCALATION_POINTS = 3
REPEAT_POINTS = 2
REPEAT_CUSTOMER_MSGS = 4
HIGH_AT, MEDIUM_AT = 9, 5
MAX_POINTS = 3 + 3 + 3 + 2 + SENTIMENT_POINTS + ESCALATION_POINTS + REPEAT_POINTS


@dataclass(frozen=True)
class Assessed:
    ticket_id: str
    url: str
    subject: str
    customer: str
    tier: str
    risk_points: int
    risk_level: str
    factors: tuple[RiskFactor, ...]
    age_days: int | None


def tier_of(hint: str) -> str:
    text = (hint or "").casefold()
    return next((t for t in TIERS if re.search(rf"(?<![a-z]){t}(?![a-z])", text)), "unknown")


def level_of(points: int) -> str:
    return "high" if points >= HIGH_AT else "medium" if points >= MEDIUM_AT else "low"


def escalation_hits(ticket: Ticket, phrases: Sequence[str]) -> tuple[str, ...]:
    """Escalation phrases in the customer's own comments (never the agent's), casefolded, whole phrases."""
    text = " ".join(m.text for m in ticket.comments if m.author_role == "customer").casefold()
    return tuple(p for p in phrases if re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", text))


def assess(ticket: Ticket, facts: TicketFacts, sentiment: Sentiment | None, phrases: Sequence[str]) -> Assessed:
    tier = tier_of(ticket.tier_hint)
    factors = tuple(f for f in (
        _tier(tier), _priority(ticket.priority), _age(facts.age_days), _unanswered(facts),
        _sentiment(sentiment), _escalation(ticket, facts, phrases), _repeat(facts)) if f is not None)
    points = sum(f.points for f in factors)
    return Assessed(ticket_id=ticket.id, url=ticket.url, subject=ticket.subject,
                    customer=ticket.organization or "unknown", tier=tier, risk_points=points,
                    risk_level=level_of(points), factors=factors, age_days=facts.age_days)


def _tier(tier: str) -> RiskFactor | None:
    return RiskFactor("tier", TIER_POINTS[tier], tier) if tier in TIER_POINTS else None


def _priority(priority: str) -> RiskFactor | None:
    points = PRIORITY_POINTS.get((priority or "").casefold(), 0)
    return RiskFactor("priority", points, priority.casefold()) if points else None


def _age(days: int | None) -> RiskFactor | None:
    if days is None:
        return RiskFactor("age", 0, "unknown (no creation date)")
    points = next((p for at_least, p in AGE_STEPS if days >= at_least), 0)
    return RiskFactor("age", points, f"{days} days") if points else None


def _unanswered(facts: TicketFacts) -> RiskFactor | None:
    hours = facts.hours_unanswered
    if hours is None:
        return RiskFactor("unanswered", 0, "unknown (comments not read)")
    points = next((p for more_than, p in UNANSWERED_STEPS if hours > more_than), 0)
    return RiskFactor("unanswered", points, f"{hours}h") if points else None


def _sentiment(sentiment: Sentiment | None) -> RiskFactor | None:
    if sentiment is None or sentiment.sentiment != "negative" or not sentiment.frustration_quote:
        return None
    return RiskFactor("negative", SENTIMENT_POINTS, f'"{sentiment.frustration_quote}"')


def _escalation(ticket: Ticket, facts: TicketFacts, phrases: Sequence[str]) -> RiskFactor | None:
    if not facts.comments_known:
        return RiskFactor("escalation language", 0, "unknown (comments not read)")
    hits = escalation_hits(ticket, phrases)
    return RiskFactor("escalation language", ESCALATION_POINTS, ", ".join(f'"{h}"' for h in hits)) if hits else None


def _repeat(facts: TicketFacts) -> RiskFactor | None:
    if facts.reopen_count >= 1:
        return RiskFactor("reopened", REPEAT_POINTS, f"{facts.reopen_count}x")
    if facts.customer_msgs >= REPEAT_CUSTOMER_MSGS:
        return RiskFactor("repeated messages", REPEAT_POINTS, f"{facts.customer_msgs} customer messages")
    return None


def _id_key(ticket_id: str) -> tuple[int, int, str]:
    return (0, int(ticket_id), "") if ticket_id.isdigit() else (1, 0, ticket_id)


def _tier_rank(tier: str) -> int:
    return TIERS.index(tier) if tier in TIERS else len(TIERS)


def rank(rows: Sequence[Assessed]) -> tuple[Assessed, ...]:
    """At-risk (high and medium) only: points desc, then age desc, then tier, then ticket id."""
    at_risk = [r for r in rows if r.risk_level in ("high", "medium")]
    return tuple(sorted(at_risk, key=lambda r: (-r.risk_points, -(r.age_days or 0), _tier_rank(r.tier),
                                                _id_key(r.ticket_id))))
