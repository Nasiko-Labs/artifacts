"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "meeting-roi"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Audits your recurring meetings over the last 4 weeks (7 to 56 days if you ask): for each series it "
    "reads your calendar (RSVPs, size, hours), looks for a notes document and the decisions or action items "
    "recorded in it, and counts chat follow-up in the meeting's channel (never message text). It proposes, "
    "from fixed rules you can read, which to cancel, shorten, shrink or keep, with the facts behind each and "
    "the person-hours a month each change would free. Attendance is RSVP-based. It never cancels, edits, "
    "declines or sends anything: a cancel is a proposal plus an optional note you can copy and send yourself. "
    "One-on-ones and meetings with external guests are not audited."
)

SKILL_ID = "meeting-roi"
SKILL_NAME = "Recurring meeting audit"
SKILL_DESCRIPTION = (
    "Scores each recurring meeting on RSVP acceptance, size, person-hours, notes, recorded decisions and chat "
    "follow-up, and proposes cancel / shorten / shrink / keep with the evidence and the hours saved. Read-only."
)
SKILL_TAGS = ["meetings", "calendar", "time audit", "engineering manager", "leadership", "read-only"]
SKILL_EXAMPLES = [
    "Audit my recurring meetings",
    "Which of the meetings I organize could be cancelled?",
    "How many hours do I spend in meetings with no notes?",
]
