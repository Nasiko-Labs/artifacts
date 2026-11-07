"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "ticket-triager"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Triages a batch of support tickets you paste into the chat (a CSV export, a JSON array, or blocks like "
    "'Ticket #123: ...'): for each ticket a priority (P1-P4), a category, an escalation risk (low/medium/high), "
    "a suggested owner team and a one-line reason quoting the ticket. The model only reads each ticket and "
    "reports signals (service down, data loss, security, churn or legal threat, blocked work, repeat contact...) "
    "with exact quotes, checked in code; priority, risk and owner are computed by fixed rules in code from those "
    "signals and the tier, age and contact count you pasted. Optional lines: 'SLA: P1 1h, P2 4h', 'Routing: "
    "billing -> Billing Ops', 'as of 2026-11-04'. Follow-ups: only P1, high risk only, sort by risk, why is #4812 "
    "P2?, as json. Up to 200 tickets per batch. It never replies to customers, never changes a ticket and never "
    "invents a customer, tier or owner."
)

SKILL_ID = "triage-tickets"
SKILL_NAME = "Triage support tickets"
SKILL_DESCRIPTION = (
    "Priority, category, escalation risk, suggested owner and a quoted reason for each pasted support ticket, "
    "computed by rules in code from quoted signals, plus filters and explanations on the same batch."
)
SKILL_TAGS = ["support", "tickets", "triage", "priority", "escalation", "routing", "customer support"]
SKILL_EXAMPLES = [
    "Triage these 30 tickets: <paste the CSV export>",
    "Only P1 and P2",
    "Why is 4812 high risk?",
]
