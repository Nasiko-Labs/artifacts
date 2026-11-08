"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "escalation-predictor"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Scores your open support-desk tickets for the risk that they escalate (priority, age, customer tier, "
    "unanswered customer messages, frustration, escalation language like \"cancel\" or \"manager\", reopens), "
    "ranks them with a transparent points breakdown, and matches each at-risk ticket to known open bugs in your "
    "tracker so the support agent can send a status update. The score is computed in code, never by the model. "
    "On request it drafts a copyable status reply that never promises a fix date. Read-only: it never sends a "
    "reply or changes a ticket. Works on pasted tickets when no support desk is connected. Not a ticket triager, "
    "not a reply sender and not a bug-fix announcer."
)

SKILL_ID = "ticket-escalation-risk"
SKILL_NAME = "Predict which support tickets will escalate"
SKILL_DESCRIPTION = (
    "Ranks open support tickets by escalation risk with the reason for every point, links the risky ones to "
    "known open bugs, and drafts a copyable status reply on request."
)
SKILL_TAGS = ["support tickets", "escalation", "risk", "customers", "bugs", "support desk", "tracker"]
SKILL_EXAMPLES = [
    "Which tickets are about to escalate?",
    "At-risk enterprise tickets this month",
    "Draft a status reply for #4812",
]
