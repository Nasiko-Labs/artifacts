"""The agent card served at /.well-known; must match AgentCard.json (tests enforce it).

Workflow generation and routing see only the name and descriptions, so both jobs are spelled out:
meeting cards from the user's own calendar (read-only), and the renewal brief from supplied inputs."""

AGENT_NAME = "Meeting Prep"
AGENT_VERSION = "2.0.0"
AGENT_DESCRIPTION = (
    "Two jobs. (1) Meeting cards: reads your calendar for today (or tomorrow, or a weekday you name) and, "
    "for each meeting with other people, pulls who is coming, your recent email and chat with them, the "
    "documents and tickets linked in the invite, and the last time you met. One card per meeting: purpose, "
    "people, last time, what changed since, open items between you, and three suggested questions, every "
    "line cited to a message, document or event it read. It is read-only: it never sends, posts or edits "
    "anything. (2) Renewal brief: writes a renewal brief for an upcoming renewal conversation from supplied "
    "inputs that are pasted in or handed over by a workflow (a market briefing, the account's recent news, "
    "and GitHub research takeaways); in that mode it does not search or call tools, and every talking point "
    "cites one of the supplied items. It does not draft emails or messages and does not review contracts."
)

SKILL_ID = "renewal-brief"
SKILL_NAME = "Renewal brief"
SKILL_DESCRIPTION = (
    "Writes the brief from supplied inputs: context, the news items with their sources, who we "
    "are meeting, exactly three talking points each citing a supplied item, and what to do "
    "before the meeting. It does not search; run the research agents first and paste their "
    "answers in."
)
SKILL_TAGS = ["renewal", "brief", "meeting prep", "talking points", "citations"]
SKILL_EXAMPLES = [
    (
        "Build a renewal brief I can walk in with. Account: Northwind. Renewal conversation "
        "tomorrow 10:00-10:30 with their platform lead. Category briefing: ... Account news: ... "
        "Engineering footprint: skipped"
    ),
    "Turn these three research outputs into a renewal brief with cited talking points",
]

CARDS_SKILL_ID = "meeting-cards"
CARDS_SKILL_NAME = "Meeting cards"
CARDS_SKILL_DESCRIPTION = (
    "Prep cards for your own meetings today (or tomorrow, or a named weekday): purpose, people, what was "
    "discussed last time, what changed since, open items between you, and up to three suggested questions, "
    "each cited to the email, chat, document, ticket or calendar event it came from. Read-only: it never "
    "sends or edits anything."
)
CARDS_SKILL_TAGS = ["meeting prep", "calendar", "pre-read", "attendees", "open items", "read-only"]
CARDS_SKILL_EXAMPLES = [
    "Prep me for my meetings today",
    "Prep the 3pm",
    "What do I need to know for tomorrow's meetings?",
]
