"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "revenue-anomaly-explainer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Checks a day of subscription revenue movement in your own payments account (default: yesterday, UTC; also "
    "a named date or the last N days, up to 7) against a trailing baseline (28 days by default, 14 to 60): new, "
    "churned, expansion, contraction and net new MRR plus failed payments, per currency. Every number, z-score "
    "and threshold is computed in code (robust median/MAD score, a money floor so tiny moves never trigger), "
    "the accounts behind each flagged move are named with only the facts the data shows (a cancellation reason, "
    "a plan change, a decline code), and a short chat post is drafted. It never claims a cause the data cannot "
    "show, never changes anything in the payments account, and posts nothing until you approve that exact text."
)

SKILL_ID = "revenue-anomaly"
SKILL_NAME = "Revenue anomaly check"
SKILL_DESCRIPTION = (
    "Compares a day's new, churned, expansion, contraction and net new MRR and failed payments against a "
    "trailing baseline, computed in code from your subscriptions, flags what is unusual, names the accounts "
    "behind each move, and drafts a chat post that is sent only after you approve it."
)
SKILL_TAGS = ["revenue", "mrr", "anomaly", "churn", "failed payments", "finance"]
SKILL_EXAMPLES = [
    "Any revenue anomalies yesterday?",
    "Check the last 3 days",
    "Post it to #revenue",
]
