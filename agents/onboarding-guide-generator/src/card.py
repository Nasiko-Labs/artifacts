"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "onboarding-guide-generator"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Builds a repository walkthrough for a new hire and their first ticket, from what the repository actually "
    "contains, read from your own connected code host (plus a tracker and doc store when connected): README, "
    "contributing and architecture docs, the directory layout, CODEOWNERS, recent merged pull requests on the "
    "ticket's area and the ticket itself. The guide says where to start, how to run things (commands copied "
    "verbatim from the docs), who to ask (code owners and recent authors, counted in code) and what to read "
    "first. Read-only: it never writes to the repository, docs or tracker, and never invents commands, paths "
    "or people. Not a code reviewer or a ticket triager."
)

SKILL_ID = "onboarding-guide"
SKILL_NAME = "Onboarding guide"
SKILL_DESCRIPTION = (
    "A new-hire walkthrough of one repository: what it is, how to run it (verbatim commands), a map of the "
    "repo, where to start on the first ticket, who to ask, recent pull requests and docs worth reading, and a "
    "first-week checklist."
)
SKILL_TAGS = ["onboarding", "new hire", "repository walkthrough", "first ticket", "getting started", "code owners"]
SKILL_EXAMPLES = [
    "Onboarding guide for Priya in acme/shop, first ticket SHOP-231",
    "Walk me through acme/api",
    "Who should I ask about src/billing?",
]
