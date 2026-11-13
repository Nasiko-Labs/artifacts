"""Pure: the one-line reason, built in code from the checked signals and verbatim quotes. Never model prose."""

from __future__ import annotations

from dataclasses import replace

from model import LEXICON_ONLY, NOT_ANALYSED, Signal, Ticket

MAX_QUOTES = 2
QUOTE_SHOWN = 80
# Most decisive first: these lead the reason.
ORDER = ("security_concern", "data_loss", "legal_threat", "service_down", "widespread", "blocked_work", "churn_threat",
         "exec_involved", "money_at_stake", "deadline", "repeat_contact", "angry_tone", "demands_priority",
         "workaround_exists")


def _ranked(signals: tuple[Signal, ...]) -> list[Signal]:
    return sorted(signals, key=lambda s: ORDER.index(s.name) if s.name in ORDER else len(ORDER))


def _quote(text: str) -> str:
    clean = " ".join(text.split()).replace('"', "'")
    return clean if len(clean) <= QUOTE_SHOWN else clean[:QUOTE_SHOWN - 1].rstrip() + "…"


def signal_part(ticket: Ticket) -> str:
    ranked = _ranked(ticket.signals)
    if not ranked:
        return f"{ticket.category}, no escalation signals"
    quoted = [f'{s.name} ("{_quote(s.quote)}")' for s in ranked[:MAX_QUOTES]]
    return " + ".join([*quoted, *(s.name for s in ranked[MAX_QUOTES:])])


def build(ticket: Ticket) -> str:
    parts = [signal_part(ticket), "tier unknown" if ticket.tier == "unknown" else ticket.tier]
    if ticket.contacts >= 2:
        parts.append(f"{ticket.contacts} contacts")
    if ticket.past_sla:
        parts.append("past SLA")
    points = ", ".join(f"+{n} {label}" for label, n in ticket.risk_points)
    parts.append(f"risk {ticket.escalation_risk}" + (f" ({points})" if points else ""))
    if ticket.analysis in (LEXICON_ONLY, NOT_ANALYSED):
        parts.append(ticket.analysis)
    return f"{ticket.priority}: " + "; ".join(parts)


def with_reason(ticket: Ticket) -> Ticket:
    return replace(ticket, reason=build(ticket))


def explain(ticket: Ticket) -> str:
    """The full rule lines for "why is #X ...?"."""
    head = (f"**{ticket.label}** — {ticket.priority}, {ticket.category}, risk {ticket.escalation_risk}, "
            f"suggested owner {ticket.suggested_owner}")
    lines = [head, f"- Priority {ticket.priority}: {ticket.priority_rule}."]
    if ticket.category_rule:
        lines.append(f"- Category: {ticket.category_rule}.")
    points = ", ".join(f"+{n} {label}" for label, n in ticket.risk_points) or "no risk points"
    total = sum(n for _, n in ticket.risk_points)
    lines.append(f"- Risk {ticket.escalation_risk}: {points} = {total} (high >= 5, medium 2-4).")
    for s in _ranked(ticket.signals):
        lines.append(f'- {s.name}: "{_quote(s.quote)}"' + (" (keyword match)" if s.source == "lexicon" else ""))
    if ticket.assignee:
        lines.append(f"- Currently assigned: {ticket.assignee}.")
    if ticket.analysis != "model":
        lines.append(f"- This ticket is {ticket.analysis}: only keyword signals were used.")
    return "\n".join(lines)
