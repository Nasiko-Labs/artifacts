"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "kudos-collector"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Collects concrete, linked evidence of good work for the people you name (up to 8), or for the people "
    "who merged pull requests in a repository you name (up to 10), over a review period (default 90 days, "
    "14 to 180; \"this half\", \"last quarter\", \"since July\"). For each person it lists notable merged pull "
    "requests with the rule that made each one notable (size, substantive review, a linked bug or customer "
    "issue, a feature label), shipped features, and praise written about them in chat channels you can see, "
    "quoted verbatim with who said it and a link. The result is an evidence packet per person for reviews and "
    "promotion cases. Read-only: it never messages, thanks or notifies anyone and never posts or writes "
    "anywhere. It never ranks, scores or compares people, never invents praise or impact, ignores direct "
    "messages, and refuses rather than guessing when the tools are not connected (pasted evidence is accepted)."
)

SKILL_ID = "kudos-collector"
SKILL_NAME = "Kudos evidence collector"
SKILL_DESCRIPTION = (
    "Per-person evidence packets over a review period: notable merged pull requests (with why), shipped "
    "features and verbatim praise from chat channels, every item linked. Read-only; no ranking or scores; "
    "never messages anyone."
)
SKILL_TAGS = ["kudos", "performance review", "promotion case", "manager", "pull requests", "praise", "read-only"]
SKILL_EXAMPLES = [
    "Kudos for Priya and Sam since July",
    "Collect wins for the payments team this half",
    "Evidence for Sam's promo case",
]
