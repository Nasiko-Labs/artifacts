"""The agent card served at /.well-known; must match AgentCard.json (tests enforce it)."""

AGENT_NAME = "Category Briefing"
AGENT_DESCRIPTION = (
    "A CrewAI crew (researcher, then editor) that reads the market this week: what enterprise "
    "buyers are reacting to in the market category you name (cloud data warehouses, "
    "observability, ...), or by default in AI cost, agent governance and software security. Five "
    "sourced items from the last seven days, each with headline, source, date and URL, plus the "
    "one item that should change a renewal conversation. The market, not a named account: it does"
    " not research one company, GitHub or a contract. It uses only the search tools granted to it"
    " and refuses rather than answer from memory."
)
SKILL_ID = "market-pulse"
SKILL_NAME = "Market pulse"
SKILL_DESCRIPTION = (
    "The market this week: what buyers are reacting to in the category you name, or by default in"
    " AI cost, agent governance and software security. Five items from the last seven days with "
    "source, date and URL, and the one that should change a renewal conversation. Not a named "
    "account."
)
SKILL_TAGS = ["market", "this week", "buyers", "AI cost", "agent governance", "software security"]
SKILL_EXAMPLES = [
    ("What are enterprise buyers reacting to this week in AI cost, agent governance, and software "
    "security? Five items from the last seven days. For each: headline, source, date, URL. Then "
    "name the one item that should change a renewal conversation tomorrow."),
    "What did the market react to this week that I should bring into a renewal call?",
    ("What are enterprise buyers reacting to this week in the cloud data warehouse category? "
    "Five items from the last seven days, with source, date and URL, and the one that should "
    "change a renewal conversation."),
]
