"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "sheet-watcher"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Watches a tracking spreadsheet on demand: reads the sheet you name (or a table you paste), compares it in "
    "code with the snapshot it saved the last time you asked in this conversation, and lists the events: rows "
    "whose status changed, dates that are approaching or overdue, required cells that are blank, and new or "
    "removed rows. Rules come from your own words (\"Status changes, Due within 3 days, Owner blank\") with "
    "sensible defaults. After you approve the exact text, it posts a short alert to one chat channel. It never "
    "edits the sheet, never invents a change, and never posts without approval. It has no schedule: it only "
    "knows what changed between two checks you ask for in the same conversation."
)

SKILL_ID = "sheet-watch"
SKILL_NAME = "Sheet watch"
SKILL_DESCRIPTION = (
    "On-demand diff of a tracking sheet against the last check in this conversation, computed in code: status "
    "changes, approaching and overdue dates, blank required cells, new and removed rows, prioritised, with "
    "repeats marked. Posts a templated alert to a chat channel after you approve it."
)
SKILL_TAGS = ["spreadsheet", "tracker", "changes", "deadlines", "alerts", "data analyst"]
SKILL_EXAMPLES = [
    "Watch the Project Tracker: Status changes, Due within 3 days, Owner blank",
    "What changed since last time?",
    "Post it to #pmo",
]
