"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "question-deduplicator"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Before you ask a question in team chat, checks whether it was already asked and answered there. It "
    "searches your chat history (the last 12 months by default, 1 to 36 if you say so, or only the channels "
    "you name), reads the threads that match, and returns up to 3 prior answers: the original question and "
    "the answer as exact quotes, who answered, when, the channel and a link, labelled strong or partial match, "
    "acknowledged or not, and 'may be out of date' when older than 180 days. When nothing matches it says so "
    "and gives a cleaned-up copy of your question to post yourself. Read-only: it never posts the question and "
    "never answers it from its own knowledge. Without a chat connection it checks pasted threads instead."
)

SKILL_ID = "prior-answers"
SKILL_NAME = "Prior answers"
SKILL_DESCRIPTION = (
    "Finds earlier chat threads that already answer your question: verbatim question and answer, who, when, "
    "channel, link, match strength (strong/partial), acknowledged, stale flag; or 'no prior answer found' with "
    "a restated question. Read-only."
)
SKILL_TAGS = ["chat", "search", "duplicate questions", "prior answers", "knowledge reuse", "read-only"]
SKILL_EXAMPLES = [
    "Has anyone asked how we rotate the staging DB password?",
    "Check if the eu-west deploy timeout was discussed",
    "Was the refund cap decided?",
]
