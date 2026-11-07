"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "standup-generator"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Builds a team standup you can copy: for the people you name (or everyone active in the repositories you "
    "name, up to 15) and a time window (default since the last working day: 24 hours, 72 hours on a Monday; "
    "or \"last 3 days\", \"since Friday\", \"this week\"), it reads pull requests, reviews, checks and commits "
    "from the code host and issues from the tracker, and lists per person what shipped, what is in progress "
    "and what is blocked (changes requested, failing checks, merge conflict, no review for 2+ days, a blocked "
    "issue, or a comment like \"waiting on\"). Every line links to a real pull request or issue read this "
    "turn; dates, counts and blocked rules are computed in code, times in UTC. Follow-ups reuse what it read: "
    "\"only maya\", \"shorter\", \"who is blocked\", \"add reviews\", \"make it chat-ready\". Read-only: it never "
    "posts the standup, comments or changes an issue. Without connected tools it formats pasted notes."
)

SKILL_ID = "standup-generator"
SKILL_NAME = "Team standup"
SKILL_DESCRIPTION = (
    "Per-person Shipped / In progress / Blocked standup from pull requests, commits and tracker issues over "
    "a time window, every line linked to its source, with a team header of counts. Read-only; copyable markdown."
)
SKILL_TAGS = ["standup", "team", "pull requests", "issues", "blocked work", "engineering manager", "read-only"]
SKILL_EXAMPLES = [
    "Standup for maya, liam and jo in acme/shop",
    "What did the team ship since Friday in acme/shop?",
    "Who is blocked this week in acme/shop?",
]
