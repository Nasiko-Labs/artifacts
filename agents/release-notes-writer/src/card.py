"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "release-notes-writer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns the merged PR titles, commits or shipped issues you paste into customer-facing release notes "
    "grouped by theme (breaking changes and security fixes first), plus a Slack announcement and a customer "
    "email draft. It says only what each change states: no invented benefits, numbers, versions, dates or "
    "links; an unknown version or date stays a placeholder like [version?]. Internal work (chore, CI, tests, "
    "dependency bumps) is left out and counted. Revise on request (\"drop item 4\", \"shorter Slack post\", "
    "\"audience: developers\"). It only drafts: it reads no repository and sends or posts nothing; you copy "
    "the text. Not for general writing, marketing copy or roadmaps."
)

SKILL_ID = "write-release-notes"
SKILL_NAME = "Write release notes"
SKILL_DESCRIPTION = (
    "Customer-facing release notes, a Slack post and a customer email from pasted changes (conventional "
    "commits, PR lists, CSV/JSON exports, issue titles), grouped by theme, with internal changes left out "
    "and counted. Draft only: nothing is sent."
)
SKILL_TAGS = ["release notes", "changelog", "announcement", "slack post", "customer email", "product"]
SKILL_EXAMPLES = [
    "Write release notes from these merged PRs: (then paste them, one per line)",
    "Make the Slack post shorter",
    "Audience: developers, include the breaking change first",
]
