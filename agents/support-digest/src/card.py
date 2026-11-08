"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "support-digest"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Builds a support digest for a period (default the last 7 days) from your support desk: ticket volume with "
    "day-by-day counts and the change against the previous period, the top themes with counts, SLA breaches "
    "(first reply and resolution, including tickets still waiting), the worst offenders, top customers by volume "
    "and the backlog. Every number, date and breach is computed in code from ticket timestamps; the model only "
    "labels ticket subjects into themes. SLA targets come from the desk's policies, else from stated defaults. "
    "Filters by tier, customer or priority. Read-only: it never changes a ticket. Works on pasted tickets when "
    "no support desk is connected. Not a ticket triager, not a reply drafter and not an escalation predictor."
)

SKILL_ID = "support-digest"
SKILL_NAME = "Support digest: volume, themes and SLA breaches"
SKILL_DESCRIPTION = (
    "Reports support ticket volume and its trend, the top themes and the SLA breaches for a period, with every "
    "number computed in code."
)
SKILL_TAGS = ["support", "tickets", "digest", "SLA", "themes", "volume", "support desk"]
SKILL_EXAMPLES = [
    "Support digest for last week",
    "How did support do yesterday?",
    "Which tickets breached SLA this week?",
]
