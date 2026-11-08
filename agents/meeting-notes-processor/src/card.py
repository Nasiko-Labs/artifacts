"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "meeting-notes-processor"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns a meeting transcript or raw meeting notes you paste into structured notes: the attendees with "
    "their share of the talking, topic notes, the decisions, and the action items and follow-ups with owners "
    "and due dates. It reads speaker and timestamp transcripts (Zoom, Teams, Otter style, VTT, SRT) and plain "
    "bullet notes. Every decision and action cites the turn and time it came from and a verbatim quote, "
    "checked in code; it never invents an owner or a date, and resolves hand-offs ('Mei, can you take that?' "
    "'On it') to the person who accepted. Follow-ups: only decisions, only action items, only mine (after "
    "'I'm Raj'), only follow-ups, show sources, as a table, as json, or a question about the meeting. Up to "
    "about 20k words. It sends nothing and reads only what you give it."
)

SKILL_ID = "process-meeting-notes"
SKILL_NAME = "Process meeting notes"
SKILL_DESCRIPTION = (
    "Attendees, topic notes, decisions, action items and follow-ups (owner, due, time) from a pasted meeting "
    "transcript or notes, each backed by a quote from its turn, plus follow-ups on the same meeting."
)
SKILL_TAGS = ["meeting notes", "transcript", "minutes", "decisions", "action items", "follow-ups", "owners",
              "attendees"]
SKILL_EXAMPLES = [
    "Process this transcript: <paste>",
    "Notes from the sync, I'm Raj: <paste>",
    "What did we decide about pricing?",
]
