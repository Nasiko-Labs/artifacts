"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "inbox-to-linear"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Scans your recent inbox (last 3 days by default, 1 to 14 if you ask) for emails that are really work "
    "requests: someone asking you or your team to do, fix, build, change, review or investigate something "
    "concrete. For each one it proposes an issue for your issue tracker (an imperative title, a description "
    "with the verbatim ask and a link back to the email, the team, existing labels found in the email, and a "
    "priority or due date only when the email says so) and checks the tracker for an issue that already "
    "tracks it. It creates each issue only after you approve that exact issue, one card per issue, then "
    "drafts a short 'tracked, here is the link' reply and sends it only after you approve that too. "
    "Newsletters, receipts, FYIs and invites are left alone; it never invents a priority, assignee or due "
    "date. Paste an email if your inbox is not connected. Not an inbox triage or a task manager."
)

SKILL_ID = "inbox-to-issues"
SKILL_NAME = "Inbox requests to issues"
SKILL_DESCRIPTION = (
    "Work requests from your recent inbox (3 days by default, 1-14): one proposed tracker issue per request "
    "with the verbatim ask and the email link, duplicates flagged as already tracked, each issue created only "
    "after you approve it, then a short reply with the issue link sent only after you approve it."
)
SKILL_TAGS = ["inbox", "email", "issue tracker", "work requests", "issues", "triage to tracker"]
SKILL_EXAMPLES = [
    "Turn my requests into issues",
    "Scan the last 5 days and put them in the Platform team",
    "Dry run, just show me",
]
