"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "competitor-change-watcher"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds what changed in competitor changelogs and pricing pages you paste (one block per competitor and "
    "source, up to 100,000 characters): new entries since your last paste in this conversation, or the "
    "difference between a Before and an After text, with price changes per plan (old, new, percent) computed "
    "in code. Each change is classified (feature, pricing, integration, deprecation, security, other) with a "
    "verbatim quote, and mapped to the roadmap issues in your connected issue tracker that it affects, marking "
    "ones you already shipped. Read-only: it never opens web pages itself, posts or edits issues, and never "
    "invents a change, a price or a roadmap link. Follow-ups: questions about the report, as json. Not a "
    "market-research or news agent."
)

SKILL_ID = "competitor-changes"
SKILL_NAME = "Competitor changes"
SKILL_DESCRIPTION = (
    "New competitor changelog entries and price changes from pasted text, classified with verbatim quotes and "
    "mapped to the roadmap issues they affect."
)
SKILL_TAGS = ["competitors", "changelog", "pricing", "release notes", "roadmap", "product management",
              "competitive intelligence"]
SKILL_EXAMPLES = [
    "Here are two changelogs and a pricing page, what changed?",
    "Map these competitor changes to our roadmap",
    "Did Acme change pricing?",
]
