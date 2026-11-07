"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from the other agents: routing and workflow generation
see only the name, description and skills."""

AGENT_NAME = "pr-agent"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Reviews GitHub pull requests, like PR-Agent: give it a PR link or owner/repo#123 and a command. "
    "/review (default) returns a reviewer guide with estimated review effort, relevant tests, security "
    "concerns and key issues tied to file and line; /describe writes a title, PR type, summary and a "
    "walkthrough of the changed files; /improve suggests concrete code changes with existing and improved "
    "code; /ask answers a question about the PR. It reads the PR and its diff through the GitHub tools the "
    "platform grants it, compresses large diffs to fit the model and says which files it left out. It posts "
    "to GitHub only when asked and only after you confirm. Not company news, not general web research."
)

SKILL_ID = "pull-request-review"
SKILL_NAME = "Pull request review"
SKILL_DESCRIPTION = (
    "One GitHub pull request: review it (effort, tests, security, key issues), describe it (title, type, "
    "summary, file walkthrough), suggest code improvements, or answer a question about its code changes. "
    "Optionally posts the result as a PR comment after confirmation."
)
SKILL_TAGS = [
    "pull request", "code review", "github", "pr review", "pr description", "code suggestions",
    "diff", "pr-agent",
]
SKILL_EXAMPLES = [
    "Review this PR: https://github.com/octocat/Hello-World/pull/42",
    "/describe octocat/Hello-World#42",
    "/improve https://github.com/acme/api/pull/118",
    "/ask Does this PR handle the empty-list case? acme/api#118",
    "Review acme/api#118 and post the review as a comment on the PR",
]
