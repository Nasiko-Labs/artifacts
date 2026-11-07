"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "promise-extractor"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds the commitments you made in text you paste (your sent emails, a chat export, meeting notes): "
    "\"I'll send it Friday\", \"let me check and get back to you\". Lists each one with to whom, what, the "
    "due date and the exact quote, and which are overdue as of a date. Due dates are worked out in code from "
    "the message date; every quote is checked against your text, and nothing it can't find is shown. "
    "Follow-ups: as of another date, only overdue, only ones to a person, show sources, as json. It reads "
    "only what you paste: no mail or chat access, it never sends anything, and no general questions."
)

SKILL_ID = "extract-promises"
SKILL_NAME = "Extract promises"
SKILL_DESCRIPTION = (
    "The commitments you made in pasted messages or notes (to whom, what, due, quote), with overdue ones "
    "as of a date, plus follow-ups on the same list."
)
SKILL_TAGS = ["promises", "commitments", "follow-ups", "overdue", "sent mail", "meeting notes"]
SKILL_EXAMPLES = [
    "Here are my sent emails from this week: <paste>",
    "Find what I promised in these notes, as of 2026-11-05: <paste>",
    "Which of my promises are overdue?",
]
