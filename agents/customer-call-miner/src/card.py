"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "customer-call-miner"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Mines this week's recorded customer calls: reads each external call's transcript, matches the call to "
    "its CRM account (deal stage, ARR from the latest closed-won deal, renewal date), and pulls out feature "
    "requests, objections, competitor mentions and churn signals as verbatim customer quotes. It groups "
    "similar requests across calls and ranks each theme by the ARR of the distinct accounts behind it, with "
    "counts, real quotes, account names and links to the call. On your approval it adds a note to each CRM "
    "account and posts a short digest to a chat channel you name; nothing is written until you approve it. "
    "Up to 12 calls per turn (say \"continue\" for the rest). Not call coaching, not sales forecasting, and "
    "not a meeting summariser."
)

SKILL_ID = "customer-call-themes"
SKILL_NAME = "Customer call themes"
SKILL_DESCRIPTION = (
    "This week's customer calls mined for feature requests, objections, competitor mentions and churn "
    "signals, with verbatim quotes, grouped into themes weighted by account ARR; then approved CRM notes "
    "per account and a digest posted to a channel you name."
)
SKILL_TAGS = ["customer calls", "call recordings", "voice of customer", "feature requests", "objections",
              "competitors", "churn", "CRM", "ARR"]
SKILL_EXAMPLES = [
    "Mine this week's customer calls.",
    "What did customers ask for last week? Post the digest to #product-feedback.",
    "Continue with the remaining calls.",
]
