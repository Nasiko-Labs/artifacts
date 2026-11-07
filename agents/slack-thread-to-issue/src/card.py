"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "slack-thread-to-issue"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Scans your chat channels (the ones you name, else your 8 most active; last 3 days by default, 1 to 14 if "
    "you ask) for threads that reached a conclusion with an action nobody has tracked yet: 'I'll fix it', "
    "'let's add a retry', 'agreed, can someone file this'. For each one it proposes an issue for your issue "
    "tracker (an imperative title, a description with the verbatim decision quote, the participants and the "
    "thread link, the team) and checks the tracker for an issue that already tracks it. Threads with no "
    "outcome, threads whose action is already done and threads that already carry an issue link are left "
    "alone. It creates each issue only after you approve that exact issue, one card per issue, then offers a "
    "fixed 'Tracked as KEY: link' reply in the thread and posts it only after you approve that too. It never "
    "sets an assignee or priority. On demand only: it cannot watch channels continuously. Paste a thread if "
    "your chat is not connected."
)

SKILL_ID = "chat-threads-to-issues"
SKILL_NAME = "Chat threads to issues"
SKILL_DESCRIPTION = (
    "Concluded chat threads with an untracked action (3 days by default, 1-14): one proposed tracker issue per "
    "thread with the verbatim decision quote and the thread link, duplicates flagged as already tracked, each "
    "issue created only after you approve it, then a 'Tracked as KEY: link' thread reply posted only after you "
    "approve it."
)
SKILL_TAGS = ["chat", "threads", "issue tracker", "action items", "issues", "follow-up"]
SKILL_EXAMPLES = [
    "Scan #platform for threads that need an issue",
    "Last 5 days, put them in the Infra team",
    "Dry run, just show me",
]
