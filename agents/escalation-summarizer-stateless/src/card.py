"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "escalation-summarizer-stateless"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Turns a messy escalation thread you paste (ticket comments, a chat or email chain, internal notes) "
    "into what the person it is escalated to needs: a one-paragraph summary, the explicit ask (what is "
    "needed from them, by when) and the customer impact, only as the thread states it. Every statement "
    "is backed by a verbatim quote from the thread, checked in code; a missing ask or impact is shown as "
    "missing, never guessed. Follow-ups: show sources, only the ask, shorter, for a given audience (the "
    "VP, the CEO), who said the deadline, as json, or a question about the thread. Stateless: it keeps "
    "nothing between messages, so follow-ups work on what is still in the recent conversation, and a very "
    "long earlier thread may need re-pasting. It does not reply to the customer, assign blame or recommend "
    "a fix, and reads only what you paste."
)

SKILL_ID = "summarize-escalation"
SKILL_NAME = "Summarize an escalation"
SKILL_DESCRIPTION = (
    "A summary paragraph, the explicit ask and the stated customer impact of the escalation thread you "
    "paste, each backed by quotes from that thread, plus follow-ups on the same thread."
)
SKILL_TAGS = ["escalation", "support", "summary", "ask", "customer impact", "ticket", "handoff"]
SKILL_EXAMPLES = [
    "Summarize this escalation for the VP: <paste the thread>",
    "What's the ask?",
    "Shorter, for the CEO",
]
