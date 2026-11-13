"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "drafter-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Writes one message from you to a specific person or group, in your tone and in the right register "
    "for where you'll send it: email, team chat, text message or LinkedIn. Tell it who the message is for, "
    "what it should achieve and the facts to include; paste a sample of your own writing and it matches "
    "your style. It revises on request (\"shorter\", \"more formal\", \"make it a chat message\"). "
    "Stateless: it keeps nothing between messages, so revisions and your tone work from what is still in the "
    "recent conversation; a long earlier draft or writing sample may need re-pasting. It never asks back: "
    "with no channel it writes an email, with no recipient it leaves [recipient?]. It never invents a fact: anything specific you didn't give it, such as a "
    "date, an amount or a name, becomes a visible placeholder like [date?]. It only drafts: it reads none "
    "of your data and sends nothing; you copy the text. Not for essays, blog posts, general questions, "
    "research or translation."
)

SKILL_ID = "draft-message"
SKILL_NAME = "Draft a message"
SKILL_DESCRIPTION = (
    "A message from you to a named person or group, for email, team chat, text or LinkedIn, in your tone "
    "and using only the facts you give; missing specifics become placeholders. Revise it on request. "
    "Draft only: nothing is sent."
)
SKILL_TAGS = ["drafting", "writing", "email", "chat message", "tone", "rewrite", "reply"]
SKILL_EXAMPLES = [
    "Email Priya, my manager, asking for a one-week extension on the Q3 deck; the review moved to Friday.",
    "Make it a chat message and more casual.",
    "Here's how I write: Hey folks, quick one - can we push sync to 3? Cheers, A.",
]
