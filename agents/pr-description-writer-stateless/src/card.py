"""Card text (stateless variant), shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "pr-description-writer-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns a diff you paste into the chat (git diff, .patch or git diff --stat) into a pull request "
    "draft: a conventional-commit title, a description that stands alone in history (summary, why, "
    "changes, test plan, risk, notes for reviewers), a checkbox test plan grounded in the changed files "
    "and a risk level computed in code (migrations, auth or payment paths, removed public symbols, "
    "config, dependencies, size). The why comes only from text you give (commit messages, ticket text); "
    "otherwise it leaves a visible placeholder. It follows a PR template you paste, never ticks a "
    "checkbox, never invents ticket numbers or test results, and revises on request (\"shorter\", \"plain "
    "title\", \"as json\"). It keeps nothing between messages: revisions work on the diff and draft still in "
    "the recent conversation, so a very long earlier diff may need pasting again. It only drafts: it "
    "reads none of your repositories and posts nothing. Not for code review, finding bugs, writing code "
    "or tests, or working from a PR link."
)

SKILL_ID = "pr-description"
SKILL_NAME = "Write a PR description from a diff"
SKILL_DESCRIPTION = (
    "A pull request title, description, checkbox test plan and risk notes from a pasted diff, with the why taken "
    "only from your own text and the risk level computed in code. Draft only: nothing is posted."
)
SKILL_TAGS = ["pull request", "PR description", "diff", "test plan", "risk", "conventional commits", "code host"]
SKILL_EXAMPLES = [
    "Write a PR description for this diff. Commit message: add refunds per #214 <paste the diff>",
    "PR description, use our template: <paste the template> then <paste the diff>",
    "Same, but shorter and no conventional commits",
]
