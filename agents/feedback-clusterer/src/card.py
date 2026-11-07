"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "feedback-clusterer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Groups customer feedback you paste into the chat (support tickets, survey answers, app reviews, call "
    "notes; a CSV export works best) into themes a PM can put on a roadmap: each with its type, item count, "
    "distinct customers, an ARR-weighted share, a trend and verbatim quotes, plus churn signals and caveats. "
    "Every count, customer, weight, trend and quote is computed or checked in code; a theme needs 3 items or "
    "2 customers to be shown. Follow-ups work on the stored result: only churn signals, top 3, enterprise "
    "only, sort by customers, show quotes, show the items of a theme, or add another batch. It reads only "
    "what you paste: no ticket system, no web, and no general questions."
)

SKILL_ID = "cluster-feedback"
SKILL_NAME = "Cluster feedback into themes"
SKILL_DESCRIPTION = (
    "Themes from the feedback you paste, with counts, distinct customers, ARR-weighted share, trend, "
    "verbatim quotes, churn signals and caveats, plus follow-ups on the same result."
)
SKILL_TAGS = ["feedback", "themes", "clustering", "voice of customer", "support tickets", "churn", "roadmap"]
SKILL_EXAMPLES = [
    "Cluster this support export and weight by ARR: <paste the CSV>",
    "Which feedback themes are rising?",
    "Only enterprise customers: what are they asking for?",
]
