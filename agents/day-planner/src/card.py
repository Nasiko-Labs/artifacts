"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "day-planner"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns your own calendar into a day plan: today (or a day you name, up to 5 days) as an ordered "
    "agenda with each meeting's time, length, attendees, location or join link and a one-line context "
    "taken from the event's own description, plus the free gaps between meetings inside your working "
    "hours and the best focus block. It flags double bookings, back-to-back runs, unanswered invitations "
    "and meetings with no description. All times and gaps are computed in code. Read-only: it never "
    "creates, moves, declines or edits an event, and it does not read email or chat. Without a calendar "
    "connection it works on meetings you paste, one per line."
)

SKILL_ID = "day-plan"
SKILL_NAME = "Day plan"
SKILL_DESCRIPTION = (
    "Your calendar for a day as an agenda with context lines, free gaps, the best focus block and flags "
    "for double bookings, back-to-back runs and unanswered invitations. Read-only."
)
SKILL_TAGS = ["calendar", "agenda", "day plan", "free time", "focus time", "double booking", "meetings"]
SKILL_EXAMPLES = [
    "plan my day",
    "when am I free tomorrow afternoon?",
    "any double bookings this week?",
]
