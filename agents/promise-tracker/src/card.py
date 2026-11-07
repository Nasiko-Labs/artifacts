"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "promise-tracker"
AGENT_VERSION = "1.0.3"
AGENT_DESCRIPTION = (
    "Finds what YOU committed to in your own sent email and chat over the last 7 days (up to 14 if "
    "you ask): \"I'll send it Friday\", \"let me check and get back to you\". It checks each one against "
    "your later messages and lists the ones still open, with who it was to, the due date, how many days "
    "overdue, and a link to the message; anything it can't back with a message you sent is left out. "
    "On request it drafts a reply that closes a commitment, on the same channel and thread, and sends "
    "it only after you approve that exact text, one draft at a time. It refuses rather than guessing "
    "when your mail and chat are not connected. Not other people's promises to you, and not a task "
    "manager or calendar."
)

SKILL_ID = "open-commitments"
SKILL_NAME = "Open commitments"
SKILL_DESCRIPTION = (
    "Your own promises from your sent email and chat in the last 7-14 days that are still open: who, "
    "what, due date, days overdue, source link. Then a drafted reply per promise, sent only after you "
    "approve the exact text."
)
SKILL_TAGS = ["promises", "commitments", "follow-up", "overdue", "sent mail", "chat", "accountability"]
SKILL_EXAMPLES = [
    "What did I promise people in the last week and not deliver yet?",
    "Anything I committed to in the last 14 days that's overdue?",
    "#2, draft it: say it slipped and I'll have it Monday.",
]
