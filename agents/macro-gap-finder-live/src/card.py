"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "macro-gap-finder-live"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reads the agent replies on recently solved tickets in your connected support desk (default last 30 days, "
    "up to 60 tickets) and finds the answers your agents keep typing by hand: each recurring answer with how "
    "many replies, tickets and agents give it (counted in code by text similarity, not by the model) and a "
    "draft macro. It checks your existing macros and reports answers they already cover instead of "
    "proposing them again. Customer names, emails, phone numbers, ticket numbers and long numbers are "
    "replaced by placeholders before any drafting, and a draft may only contain facts that at least two of "
    "its replies state. Without a connected desk, paste 10 or more replies and it uses the same method. "
    "Follow-ups: show the replies behind a pattern, a different minimum or top N, a shorter or friendlier "
    "draft. Read-only: it never creates a macro or edits a ticket, and does not answer customers or general "
    "questions."
)

SKILL_ID = "find-macro-gaps-live"
SKILL_NAME = "Find macro gaps in the support desk"
SKILL_DESCRIPTION = (
    "Recurring hand-typed answers in recent solved-ticket replies, ranked by how often they occur, minus "
    "what existing macros cover, each with a draft macro grounded in those replies; customer data scrubbed."
)
SKILL_TAGS = ["support", "macros", "canned responses", "saved replies", "help desk", "deflection", "agent replies",
              "tickets"]
SKILL_EXAMPLES = [
    "Which answers do we keep typing by hand?",
    "Macro candidates from the last 60 days",
    "Draft a macro for the SSO password question",
]
