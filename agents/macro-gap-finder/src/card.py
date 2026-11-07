"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "macro-gap-finder"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds the recurring manual answers in support replies you paste (CSV, JSON or plain text, 10 to 2,000 "
    "replies) and drafts a macro for each: the pattern, how many replies give that answer (counted in code by "
    "text similarity, not by the model) and a reusable macro text. Customer names, emails, phone numbers and "
    "long numbers are replaced by placeholders before any model sees a reply. A draft may only contain facts "
    "that at least two of its replies state; anything else is removed or the draft falls back to a real reply. "
    "Paste your existing macros too and it skips answers they already cover. Follow-ups: show the replies "
    "behind a pattern, a different minimum or top N, a shorter or friendlier draft. It creates no macro "
    "anywhere and does not answer customers or general questions."
)

SKILL_ID = "find-macro-gaps"
SKILL_NAME = "Find macro gaps"
SKILL_DESCRIPTION = (
    "Recurring answers in pasted agent replies, ranked by how often they occur, each with a draft macro "
    "grounded in those replies; customer data scrubbed first."
)
SKILL_TAGS = ["support", "macros", "canned responses", "saved replies", "help desk", "deflection", "agent replies"]
SKILL_EXAMPLES = [
    "Which of these replies should be macros? <paste the replies>",
    "Show the replies behind pattern 2",
    "Ignore anything under 5 replies",
]
