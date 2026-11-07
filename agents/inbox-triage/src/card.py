"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "inbox-triage"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reads your recent inbox (last 2 days by default, 1 to 14 if you ask) and sorts each thread into "
    "urgent (needs you today), reply (someone is waiting on you, not today-critical) and ignore (bulk, "
    "automated, FYI), each with a one-line reason that quotes the email. Clear cases are decided by rules "
    "in code (bulk senders, receipts, payment failures, deadlines today, your VIPs); only the ambiguous "
    "rest goes to the model, and an urgent call it can't back with a quote is demoted. Up to 60 threads "
    "per turn, with \"continue\" for older ones. Read-only: it never sends, archives, labels, deletes, "
    "marks read or drafts replies."
)

SKILL_ID = "inbox-triage"
SKILL_NAME = "Inbox triage"
SKILL_DESCRIPTION = (
    "Your recent inbox threads sorted into urgent, reply and ignore, each with a quoted reason, counts per "
    "bucket and the top 3 to do first. Window 2 days by default (1-14), VIP senders from your message, "
    "read-only."
)
SKILL_TAGS = ["inbox", "email", "triage", "urgent", "reply", "prioritize", "read-only"]
SKILL_EXAMPLES = [
    "Triage my inbox",
    "What's urgent since Monday?",
    "Show ignored",
]
