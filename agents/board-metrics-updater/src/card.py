"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "board-metrics-updater"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Computes your company's subscription revenue metrics for a period (default: last full calendar month; also "
    "a quarter, a named month, the last N days or explicit dates) from the subscriptions in your own payments "
    "account: MRR, ARR, the MRR bridge (new, expansion, contraction, churned), logo churn, revenue churn, net and "
    "gross revenue retention, per currency, with definitions and caveats (test mode, metered items excluded, "
    "plan history older than 30 days approximated). Every number is computed in code, never by the model. It "
    "drafts a board paragraph checked number by number against the figures, and on request appends one row to "
    "your metrics spreadsheet or the paragraph to your board document, each only after you approve that exact "
    "write. It never edits an existing row or overwrites a document, and numbers cannot be changed by chat."
)

SKILL_ID = "board-metrics"
SKILL_NAME = "Board metrics"
SKILL_DESCRIPTION = (
    "MRR, ARR, new/expansion/contraction/churned MRR, logo and revenue churn, NRR and GRR for a period, per "
    "currency, computed in code from your subscriptions, plus a checked board paragraph. Appends one row to your "
    "metrics sheet or the paragraph to your board doc, each only after you approve it."
)
SKILL_TAGS = ["mrr", "arr", "nrr", "churn", "board metrics", "saas metrics", "finance"]
SKILL_EXAMPLES = [
    "Compute board metrics for last month",
    "Write the narrative paragraph",
    "Add this month to my Board Metrics sheet",
]
