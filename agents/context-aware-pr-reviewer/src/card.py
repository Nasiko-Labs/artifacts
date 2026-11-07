"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from the plain diff reviewer: workflow generation and the
router see only the name and description."""

AGENT_NAME = "context-aware-pr-reviewer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews one pull request against what it was supposed to do, not just the diff. It reads the linked "
    "ticket's acceptance criteria and grades the PR per criterion (met, partial, not found, with the file:line "
    "evidence), looks up similar past merged PRs and the review feedback they received, and returns review "
    "comments anchored to changed lines plus a ticket alignment verdict (aligned, partial, misaligned, no "
    "ticket) computed in code. Every comment is checked against the diff; unverifiable ones are dropped. After "
    "you approve the exact text it can post one summary comment on the PR. On demand in chat; it never "
    "approves, requests changes, merges, edits the ticket or pushes code. Use it to review a PR against its "
    "ticket; for describing or improving a PR from the diff alone, use a plain PR reviewer."
)

SKILL_ID = "pr-review-against-ticket"
SKILL_NAME = "PR review against its ticket"
SKILL_DESCRIPTION = (
    "Review a pull request against its linked ticket's acceptance criteria and similar past PRs: per-criterion "
    "alignment with evidence, grounded review comments on changed lines, and one summary comment on the PR, "
    "posted only after you approve the exact text."
)
SKILL_TAGS = ["code review", "pull request", "acceptance criteria", "ticket alignment", "precedent", "developer"]
SKILL_EXAMPLES = [
    "Review acme/shop#412 against its ticket",
    "Does PR 88 do what SHOP-231 asks?",
    "Review PR 12 focusing on security",
]
