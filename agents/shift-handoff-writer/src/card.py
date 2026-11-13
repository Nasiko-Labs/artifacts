"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "shift-handoff-writer"
AGENT_VERSION = "1.0.2"
AGENT_DESCRIPTION = (
    "Turns the day's notes you paste into the chat (queue notes, ticket updates, chat jottings, a running "
    "log) into a shift handoff for the next person: what is hot and why, what is still open, what is "
    "waiting and on whom, what the next person needs to know (standing instructions and promises made to "
    "customers), and a one-line list of what was resolved. Status and hot-ness are decided by fixed rules "
    "in code over your notes, every item is backed by a quote from them, and owners, times and ticket "
    "numbers are never filled in when the notes don't say them. Follow-ups: only hot, shorter, show "
    "sources, mark #4812 resolved, add a note, as json. It reads only what you give it and never sends "
    "or posts anything."
)

SKILL_ID = "write-shift-handoff"
SKILL_NAME = "Write a shift handoff"
SKILL_DESCRIPTION = (
    "A handoff from the day's pasted notes: hot items with the reason, open and waiting items with owners as "
    "written, need-to-know instructions and promises, and what was resolved, each backed by a quote."
)
SKILL_TAGS = ["shift handoff", "support", "queue", "tickets", "on-call", "end of shift", "promises"]
SKILL_EXAMPLES = [
    "Write my shift handoff from these notes: <paste the day's notes>",
    "Only the hot items",
    "Mark #4812 resolved",
]
