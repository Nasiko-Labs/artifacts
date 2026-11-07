"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "decision-log-builder"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Scans your chat channels over the last 7 days (1 to 14 if you ask, or since Monday for 'this week') "
    "for decisions made in passing and lists each one with who decided, when, the verbatim quote, the "
    "channel and a link. Questions and proposals are left out; a decision implied by agreement is marked "
    "and never pre-selected; a later decision that reverses an earlier one is linked to it; decisions "
    "already in your decision log document are marked as duplicates. After you pick the entries and "
    "approve the exact text on a card, it appends them to the end of your running decision log (or "
    "creates a new weekly log doc). It never edits or deletes existing log entries and never writes "
    "without that approval. Not a meeting-notes or task tracker."
)

SKILL_ID = "decision-log"
SKILL_NAME = "Decision log from chat"
SKILL_DESCRIPTION = (
    "Decisions made in chat in the last 7 days (1-14): decision, who decided, date, verbatim quote, "
    "channel, link, explicit or implied, duplicates of the log marked. Appended to your decision log "
    "document only after you approve the exact entries."
)
SKILL_TAGS = ["decision log", "decisions", "chat", "channels", "documentation", "append", "team memory"]
SKILL_EXAMPLES = [
    "Build this week's decision log",
    "Decisions in #product since Monday",
    "Append them to the Team Decisions doc",
]
