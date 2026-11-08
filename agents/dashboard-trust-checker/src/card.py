"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "dashboard-trust-checker"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Audits a dashboard inventory you provide (a spreadsheet or a pasted export with name, owner, last refresh, "
    "viewers and query status) and classifies each dashboard as healthy, stale, broken, unused or unknown with a "
    "proposed action (keep, fix, review, retire) and the reasons, all computed in code from the inventory's own "
    "columns. Optionally adds trust evidence from chat complaints that name a dashboard. Read-only: it never "
    "opens a BI tool, never runs a query and never deletes anything; its findings are only as good as the "
    "inventory."
)

SKILL_ID = "dashboard-trust-audit"
SKILL_NAME = "Dashboard trust audit"
SKILL_DESCRIPTION = (
    "Flags broken, stale and unused dashboards from an inventory table (stale after 7 days or twice the stated "
    "cadence; unused at 0 viewers), proposes fix / review / retire / keep with reasons, adds verbatim chat "
    "complaints as evidence, and lists retire candidates. Read-only."
)
SKILL_TAGS = ["dashboards", "bi", "data quality", "stale", "data analyst", "audit"]
SKILL_EXAMPLES = [
    "Audit sheet BI inventory",
    "Which dashboards should we kill?",
    "Stale after 14 days",
]
