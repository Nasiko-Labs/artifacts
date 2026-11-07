"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "thread-untangler"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Untangles a Slack thread or an email chain you paste into the chat: it splits it into numbered "
    "messages with speakers, then gives a short TL;DR, the decisions (marking the ones a later message "
    "reversed), the open questions nobody answered, and the action items with owner, due as written and "
    "which ones are yours. Every item is backed by a verbatim quote from one message, checked in code; "
    "anything it can't quote is left out, and it says how many. It never invents an owner or a date. "
    "Follow-ups work on the stored thread: I'm <name>, only my items, only decisions, only open "
    "questions, only reversals, show sources, who owns what, as json, or a question about the thread. "
    "It reads only what you paste: it posts and sends nothing, and answers no general questions."
)

SKILL_ID = "untangle-thread"
SKILL_NAME = "Untangle a thread"
SKILL_DESCRIPTION = (
    "TL;DR, decisions (with reversals), open questions and action items (owner, due, yours) of a pasted "
    "Slack thread or email chain, each backed by a quote from one message, plus follow-ups on it."
)
SKILL_TAGS = ["slack thread", "email chain", "decisions", "reversals", "open questions", "action items",
              "owners", "tl;dr"]
SKILL_EXAMPLES = [
    "Untangle this Slack thread: <paste>",
    "Untangle this email chain, I'm Priya: <paste>",
    "Which decisions in this thread were reversed?",
]
