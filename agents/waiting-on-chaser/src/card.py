"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "waiting-on-chaser"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds what YOU asked other people for in your own sent email and chat over the last 14 days (3 to 30 "
    "if you ask) that has had no reply: a review, an approval, a decision, your input, a deliverable. It "
    "checks each ask for a later answer from that person and ranks who you are waiting on by days waiting, "
    "deadline, what is blocked and who they are (your manager, an outside contact), with the ask quoted, "
    "the deadline, what it blocks and a link to your message. Asks it can't back with a message you sent "
    "are left out; ones that may already be answered are listed apart and never nudged by default. On "
    "request it drafts a nudge, light or firm to match the delay, in the same thread, and sends it only "
    "after you approve that exact text, one draft at a time. Not what you owe other people, and not a "
    "task manager."
)

SKILL_ID = "waiting-on"
SKILL_NAME = "Waiting on others"
SKILL_DESCRIPTION = (
    "Your own unanswered asks from your sent email and chat in the last 14 days (3-30): who you are "
    "waiting on, what you asked, days waiting, deadline, what is blocked, source link, ranked. Then a "
    "nudge per ask, light or firm, sent only after you approve the exact text."
)
SKILL_TAGS = ["waiting on", "follow-up", "nudge", "unanswered", "sent mail", "chat", "chaser"]
SKILL_EXAMPLES = [
    "Who am I waiting on?",
    "What have I asked people for in the last 3 weeks that's still unanswered?",
    "Nudge #1 and #3, keep #3 light.",
]
