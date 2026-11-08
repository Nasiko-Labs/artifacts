"""Card text, shared by __main__.py (the served card), smalltalk.py (the intro) and AgentCard.json (checked by
a test). Workflow generation sees only the name and description, so the description says what this agent
is not."""

AGENT_NAME = "opportunity-brief"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns public signals into a short, cited opportunity brief on a topic you name (a market, a technology, "
    "a competitor space): 3 to 5 themes that look like opportunities, each with evidence links, a signal "
    "strength computed in code from points, comments, stars and recency, risks and a cheap next step. It "
    "reads Hacker News itself, public repositories through your connected code host when one is granted, "
    "and anything you paste (RSS items, Reddit threads, notes). Every quote is checked word for word "
    "against what it read, and nothing is stated without a link. Read-only: it never posts or writes "
    "anywhere, never fetches arbitrary URLs, and gives no investment advice or market sizes."
)

SKILL_ID = "opportunity-brief"
SKILL_NAME = "Opportunity brief"
SKILL_DESCRIPTION = (
    "A cited brief on a topic over the last 30 days (7-90): 3-5 opportunity themes with Hacker News, "
    "code-host and pasted evidence, a computed signal strength, risks and a next step. Follow-ups drill "
    "into a theme, shorten it or re-render it as JSON without fetching again."
)
SKILL_TAGS = ["opportunity", "trends", "signals", "market research", "hacker news", "product", "brief"]
SKILL_EXAMPLES = [
    "Opportunity brief on AI agent observability, last 30 days",
    "What's trending in developer tools? Focus on what to build",
    "Here are 6 links and notes from my feed: <paste>. What's the opportunity?",
]
