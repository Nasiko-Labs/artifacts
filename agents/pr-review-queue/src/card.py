"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "pr-review-queue"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Shows the pull request review queue for the repositories you name (owner/repo, up to 5): which open pull "
    "requests have gone quiet, who each one is waiting on (a reviewer, the author, failing checks or a merge "
    "conflict), and the review load per person (open requests, oldest wait, reviews done, overloaded or idle). "
    "Staleness, blockers, priority and load are computed in code from the code host, never guessed. After you "
    "approve the exact text it posts one nudge comment on one stale pull request. On demand in chat; it never "
    "merges, approves, closes, labels, assigns or edits a pull request."
)

SKILL_ID = "pr-review-queue"
SKILL_NAME = "PR review queue"
SKILL_DESCRIPTION = (
    "Stale pull requests in priority order, what each is blocked by (reviewer, author, failing checks, merge "
    "conflict) and the review load per person; then a nudge comment on a stale pull request, posted only after "
    "you approve the exact text."
)
SKILL_TAGS = ["pull requests", "code review", "review load", "stale prs", "engineering", "developer productivity"]
SKILL_EXAMPLES = [
    "What PRs are stuck in acme/shop?",
    "Who is overloaded with reviews in acme/api?",
    "Nudge the stalest PR.",
]
