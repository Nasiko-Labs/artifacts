"""Card text, shared by __main__.py (the served card), smalltalk.py and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "feedback-to-roadmap"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Pulls recent customer feedback from your support desk and a feedback channel in chat (plus anything you "
    "paste), clusters it into roadmap themes with item counts, distinct customers, an ARR-weighted score from "
    "the CRM, a trend and verbatim customer quotes, flags churn signals, and links each theme to the existing "
    "tracker issues that already cover it. Read-only: it never creates or edits an issue and never posts "
    "anywhere; a drafted issue is copyable text. Ask for \"feedback themes from the last 30 days\". Not a call "
    "transcript miner and not a general support assistant."
)

SKILL_ID = "feedback-to-roadmap-themes"
SKILL_NAME = "Turn customer feedback into roadmap themes"
SKILL_DESCRIPTION = (
    "Reads support tickets and chat feedback from a window (default 30 days), clusters them into themes with "
    "counts, customers, ARR-weighted share, trend and quotes, and links each theme to existing tracker issues."
)
SKILL_TAGS = ["customer feedback", "themes", "roadmap", "support desk", "chat", "CRM", "tracker", "product"]
SKILL_EXAMPLES = [
    "Feedback themes from the last 30 days",
    "What are customers asking for in #product-feedback this month?",
    "Which themes already have an issue?",
]
