"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "ci-failure-detective"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Tells you why a CI run failed. Give it a failed run, a pull request whose checks failed, or \"the latest "
    "failure on main in owner/repo\": it reads the failing job's log, checks the same job's recent run history, "
    "and gives one verdict: REAL (the likely culprit commit and author, with the changed lines the failure "
    "points at), FLAKY (\"has failed 4 of the last 20 runs, and passed on a rerun\"), or INFRA (runner, disk, "
    "network), with the log excerpt and a suggested fix when one is obvious. Counts, culprit and flakiness are "
    "computed in code, never guessed. After you approve the exact text it can comment the verdict on the pull "
    "request or open a flaky-test issue. On demand in chat; it never re-runs, reverts or pushes anything."
)

SKILL_ID = "ci-failure-verdict"
SKILL_NAME = "CI failure verdict"
SKILL_DESCRIPTION = (
    "Why a CI run or a pull request's checks failed: a REAL / FLAKY / INFRA verdict with the culprit commit or "
    "the k-of-n flake rate, the log excerpt and a suggested fix; then a pull request comment or a flaky-test "
    "issue, written only after you approve the exact text."
)
SKILL_TAGS = ["ci", "build failure", "flaky tests", "culprit commit", "pull request", "debugging", "devops"]
SKILL_EXAMPLES = [
    "Why did CI fail on PR 123 in acme/shop?",
    "What broke the latest failure on main in acme/shop?",
    "Post it on the PR.",
]
