"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from the other renewal-desk agents: workflow generation
sees only the name and description."""

AGENT_NAME = "account-news"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Reports what changed at one company in the last seven days that a renewal owner should know: "
    "product announcements, leadership changes, layoffs or hiring, and competitor mentions. At most "
    "five items, each with headline, source, date, URL and one line on the renewal risk or opening; "
    "anything without a URL is left out. It searches the web through the tools the platform grants "
    "it and refuses rather than answering from memory when no search is connected. Follow-up "
    "questions are answered from the list it gave. Not GitHub or engineering activity, and not the "
    "market or category as a whole."
)

SKILL_ID = "account-pulse"
SKILL_NAME = "Account pulse"
SKILL_DESCRIPTION = (
    "One company, the last seven days, renewal risk: up to five recent news items with source, "
    "date and URL, each with one line on the risk or opening for the renewal. Not GitHub, not the market."
)
SKILL_TAGS = ["account news", "renewal", "renewal risk", "company news", "last seven days", "account pulse"]
SKILL_EXAMPLES = [
    "What has been published about Northwind in the last seven days that a renewal owner should know? "
    "Five items max, with headline, source, date and URL.",
    "Any leadership changes, layoffs or product launches at Acme this week that affect our renewal?",
    "Which single item do I raise in the first two minutes, and which do I leave alone?",
]
