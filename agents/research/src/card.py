"""The agent card served at /.well-known; must match AgentCard.json (tests enforce it)."""

AGENT_NAME = "Research"
AGENT_DESCRIPTION = (
    "Reports a company's public GitHub footprint: its top public repositories, the languages they "
    "use, and which repositories moved in the last month, each with a github.com link. It reads "
    "GitHub only through the GitHub tools granted on the Nasiko MCP gateway. A private or missing "
    "organization is reported as private or missing, and when GitHub is not connected it says so "
    "instead of answering from memory. It does not report headlines or news."
)
SKILL_ID = "github-footprint"
SKILL_NAME = "GitHub footprint"
SKILL_DESCRIPTION = (
    "Repositories and languages of a company's public GitHub org: top repositories, languages, "
    "and recent activity, with key takeaways first. Not headlines, not news."
)
SKILL_TAGS = ["github", "repositories", "languages", "open source", "engineering footprint"]
SKILL_EXAMPLES = [
    "Look up the GitHub organization for Northwind. Top public repositories, languages, and which "
    "repos moved in the last month. Key takeaways first. If the org is private or missing, say so.",
    "Which languages does the Vercel GitHub org use most?",
]
