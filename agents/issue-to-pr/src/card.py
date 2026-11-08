"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "issue-to-pr"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Turns one small, well-described software issue into a draft pull request. It reads the issue and the "
    "relevant files through your code host connection (or an issue and files you paste), finds where the "
    "change belongs, lets a sandboxed coding engine edit only those files, computes the exact diff in code, "
    "runs the tests when it can, and shows you the diff, PR title and body. Nothing is pushed until you "
    "approve: one approval card per write (create the branch, commit each file, open the draft PR). It never "
    "touches the default branch, CI files, lockfiles or secrets, and never claims tests passed unless they "
    "ran. Not a code reviewer, merger or general programming helper."
)

SKILL_ID = "issue-to-pr"
SKILL_NAME = "Issue to draft PR"
SKILL_DESCRIPTION = (
    "Given an issue and a repository: localization, a sandboxed edit, the computed diff, a test run when "
    "possible, then a draft pull request opened step by step, each write only after its own approval."
)
SKILL_TAGS = ["issue", "pull request", "coding agent", "diff", "repository", "developer"]
SKILL_EXAMPLES = [
    "Fix demo-org/shop-api#12",
    "Take acme/web#340 and show me the diff only",
    "Fix this issue in acme/api: <paste the issue>",
]
