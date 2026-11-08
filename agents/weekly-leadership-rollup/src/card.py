"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "weekly-leadership-rollup"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Builds a cross-team weekly status rollup a leader can read in two minutes: for each tracker team you can "
    "see (up to 8), what shipped in the last 7 days, what is in flight, what is blocked and what is due next "
    "week, plus launch, review and release events from your calendar. It then runs a reality check: where the "
    "status people reported in status-like chat channels (or in an update you paste) disagrees with the "
    "tickets (reported done but still in review, on track but blocked or not updated for weeks), each item "
    "quoted with its link and framed as a question to ask, not a conclusion. Read-only: it never posts, "
    "sends or changes anything (copy the rollup yourself), never reads direct messages, and never ranks teams "
    "or people. Refuses rather than guessing when the tracker is not connected."
)

SKILL_ID = "weekly-leadership-rollup"
SKILL_NAME = "Weekly leadership rollup"
SKILL_DESCRIPTION = (
    "Per-team shipped / in flight / blocked / next week from the tracker and calendar for the last 7 days "
    "(3 to 14), and a reality check of chat-reported status against the tickets (rules D1 to D11, risk "
    "high/medium/low), with quotes and links. Read-only, copy-only; no ranking."
)
SKILL_TAGS = ["leadership", "weekly status", "rollup", "reality check", "tickets", "blocked work", "read-only"]
SKILL_EXAMPLES = [
    "Weekly leadership rollup",
    "Where does the reported status differ from the tickets for Payments?",
    "Rollup since Monday for platform and payments",
]
