"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "code-reviewer-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews a code diff you paste (unified diff, e.g. from git diff) and gives review comments by severity "
    "(critical, required, optional, nit), each tied to a changed line, with suggested replacements. "
    "A small CrewAI crew does it: a reviewer writes the comments, then a verifier discards any the diff does "
    "not support. It reviews "
    "correctness, readability, architecture, surface-level security, performance and your team's standards "
    "(paste them after \"standards:\"), and checks the change against a spec or issue text you paste after "
    "\"spec:\". The verdict (request changes, comment, or no blocking comments), size class and tests note are "
    "computed in code. Follow-ups (only criticals, show all, explain or suggest a fix for comment N, as json, "
    "re-review with a spec) work on what is still in the recent conversation; it keeps nothing between "
    "messages, so a very long earlier diff may need re-pasting. It reads only what you paste: no repository, no PR link, and it posts nothing. Not for "
    "writing new code, deep security audits, what could break outside the diff, PR descriptions or general "
    "programming questions."
)

SKILL_ID = "review-diff"
SKILL_NAME = "Review a diff"
SKILL_DESCRIPTION = (
    "Review comments by severity on a pasted diff, each tied to an added line with an optional suggested "
    "replacement; optional spec check and team standards. Read-only: nothing is posted."
)
SKILL_TAGS = ["code review", "diff", "pull request", "review comments", "standards", "spec check"]
SKILL_EXAMPLES = [
    "Review this diff: <paste git diff>",
    "Review against this spec: refunds must be partial-capable, logged, idempotent + <diff>",
    "Review with our standards: no bare except, type hints required + <diff>",
]
