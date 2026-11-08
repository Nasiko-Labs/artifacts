"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "issue-localizer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds where one software issue most likely lives in a repository: a ranked list of files, then the "
    "functions or classes, then the exact lines, with a score and the signals behind each (named in the "
    "issue, code-search hits, path match). It reads the issue and the repository through your code host "
    "connection, or works on an issue and files you paste. Every path, name and line number is checked "
    "against the repository; line numbers are computed by code, never guessed. It also lists what the "
    "report is missing (steps to reproduce, expected vs actual, version, logs) and suggests the next step. "
    "Read-only: it never edits code, opens pull requests or comments. Not a fixer, code reviewer or general "
    "programming helper."
)

SKILL_ID = "localize-issue"
SKILL_NAME = "Localize an issue"
SKILL_DESCRIPTION = (
    "Given an issue and a repository: the issue kind, suspect files with scores, suspect functions, edit "
    "locations with line ranges, what the report is missing, and the next step. Read-only."
)
SKILL_TAGS = ["issue", "bug", "triage", "localization", "repository", "code search", "developer"]
SKILL_EXAMPLES = [
    "Where is the bug in owner/repo#12?",
    "Localize this issue in acme/shop-api: <paste the issue>",
    "Triage acme/shop-api#31: what's missing and where do I look?",
]
