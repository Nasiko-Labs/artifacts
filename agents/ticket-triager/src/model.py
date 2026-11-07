"""Pure: the ticket record that flows from intake to render. Frozen; every step returns a new copy via
`dataclasses.replace`. Stored in the checkpointer as plain dicts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

CATEGORIES = ("outage", "bug", "how_to", "billing", "account_access", "security", "data_issue",
              "feature_request", "cancellation", "other")
SIGNALS = ("service_down", "data_loss", "security_concern", "blocked_work", "deadline", "churn_threat",
           "legal_threat", "exec_involved", "angry_tone", "repeat_contact", "workaround_exists",
           "demands_priority", "money_at_stake")
TIERS = ("enterprise", "business", "pro", "free", "unknown")
MODEL, LEXICON_ONLY, NOT_ANALYSED = "model", "lexicon-only", "not analysed"


@dataclass(frozen=True)
class Signal:
    name: str
    quote: str
    source: str = "model"  # model | lexicon


@dataclass(frozen=True)
class Ticket:
    id: str
    subject: str = ""
    body: str = ""
    customer: str | None = None
    tier: str = "unknown"
    created: str | None = None
    status: str | None = None
    channel: str | None = None
    assignee: str | None = None
    contacts: int = 0
    age_hours: float | None = None
    generated_id: bool = False
    # Filled by the signals step and the rules (code).
    category: str = "other"
    signals: tuple[Signal, ...] = ()
    analysis: str = MODEL
    priority: str = "P4"
    priority_rule: str = ""
    escalation_risk: str = "low"
    risk_points: tuple[tuple[str, int], ...] = ()
    past_sla: bool = False
    suggested_owner: str = ""
    reason: str = ""

    @property
    def age_days(self) -> int | None:
        return None if self.age_hours is None else int(self.age_hours // 24)

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}".strip()

    def has(self, name: str) -> bool:
        return any(s.name == name for s in self.signals)

    @property
    def is_open(self) -> bool:
        return (self.status or "").strip().casefold() not in ("closed", "solved", "resolved", "done")

    @property
    def label(self) -> str:
        return f"#{self.id}" if self.id.isdigit() else self.id


def to_dict(ticket: Ticket) -> dict:
    data = asdict(ticket)
    data["signals"] = [asdict(s) for s in ticket.signals]
    data["risk_points"] = [list(p) for p in ticket.risk_points]
    return data


def from_dict(data: Mapping[str, Any]) -> Ticket:
    known = {f.name for f in fields(Ticket)}
    values = {k: v for k, v in data.items() if k in known}
    values["signals"] = tuple(Signal(**s) for s in data.get("signals") or ())
    values["risk_points"] = tuple((str(p[0]), int(p[1])) for p in data.get("risk_points") or ())
    values["contacts"] = int(data.get("contacts") or 0)
    return Ticket(**values)


def public(ticket: Ticket) -> dict:
    """The envelope's per-ticket fields (catalog `tickets[]` plus extras)."""
    return {"id": ticket.id, "subject": ticket.subject, "customer": ticket.customer, "tier": ticket.tier,
            "age_days": ticket.age_days, "contacts": ticket.contacts, "status": ticket.status,
            "category": ticket.category, "signals": [{"name": s.name, "quote": s.quote} for s in ticket.signals],
            "priority": ticket.priority, "escalation_risk": ticket.escalation_risk,
            "suggested_owner": ticket.suggested_owner, "assignee": ticket.assignee, "reason": ticket.reason,
            "analysis": ticket.analysis, "past_sla": ticket.past_sla}
