"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "issue-hygiene-bot"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Checks your tracker for issue hygiene problems: open issues with no project, no estimate (only for teams "
    "that estimate), stale In Progress (no update for 14 days) or open 90+ days. Groups the flagged issues by "
    "owner with a team hygiene score, lists unassigned ones for you, and drafts one chat direct message per owner "
    "you pick, listing their issues with links and suggested actions. Every message is shown to you and sent "
    "only after you approve that exact text, one owner at a time. It never edits, closes or reassigns an issue, "
    "and owners' replies do not come back to it. Ask \"Hygiene check for team Platform\". Not a general tracker "
    "assistant and not an issue triager."
)

SKILL_ID = "issue-hygiene-check"
SKILL_NAME = "Issue hygiene check and owner nudges"
SKILL_DESCRIPTION = (
    "Finds open tracker issues with no project, no estimate, stale in progress or open 90+ days, groups them by "
    "owner, and drafts one direct message per picked owner, sent only after you approve the exact text."
)
SKILL_TAGS = ["issue hygiene", "tracker", "stale issues", "backlog", "owners", "direct message", "project manager"]
SKILL_EXAMPLES = [
    "Hygiene check for team Platform",
    "Which issues have been open over 90 days?",
    "Message the owners of stale In Progress issues",
]
