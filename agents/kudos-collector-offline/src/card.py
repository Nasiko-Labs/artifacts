"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "kudos-collector-offline"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Collects concrete, evidenced recognition examples per person from a PR list, Slack praise and shipped "
    "features you paste (sections PRs: / Praise: / Shipped:, CSV or JSON PR exports, optional Aliases: like "
    "'@pdev = Priya Dev', Period: Q3, People:, Min:). Each example says what the person did, when, and the "
    "evidence (PR number/link or the quote); impact only if the record states it. PR authors and feature owners "
    "are read in code; names in praise must be written in the message; first-name-only names are never merged; "
    "vague praise ('great job!') is counted, not used. People are listed alphabetically with counts of evidence "
    "items: it never ranks, scores or compares people and does not write the review (Performance Review Drafter "
    "can use its output). Follow-ups: only Priya Dev, only PRs, show the quotes, unattributed records, add these, "
    "as json. Works only on pasted data: it reads no code host or chat."
)

SKILL_ID = "kudos-collection"
SKILL_NAME = "Recognition examples per person"
SKILL_DESCRIPTION = (
    "Turns pasted PRs, praise messages and shipped features into per-person, evidenced examples for reviews or "
    "promotion cases, alphabetical and never ranked."
)
SKILL_TAGS = ["recognition", "kudos", "performance review", "promotion", "engineering management", "praise"]
SKILL_EXAMPLES = [
    "Collect kudos for my team from these PRs and Slack praise: <paste>",
    "Only Priya Dev",
    "Which praise was too general to use?",
]
