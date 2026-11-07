"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "slack-catch-up"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Catches you up on your team chat workspace: for the channels and direct messages you are in, since your "
    "last catch-up in this conversation (first time: the last 24 hours, or a window you name, 1 to 7 days), it "
    "shows per channel the messages that mention you and the decisions made first, each with the exact quote, "
    "who said it and a link, then up to 3 short highlights. Channels with mentions or decisions come first; "
    "@channel broadcasts are counted, not treated as personal mentions. Up to 12 channels and 5 direct "
    "message conversations per run, and it says which ones it could not read. Read-only: it never posts, "
    "replies, reacts, marks anything read or joins channels. Without a chat connection it summarizes pasted "
    "channel text."
)

SKILL_ID = "chat-catch-up"
SKILL_NAME = "Chat catch-up"
SKILL_DESCRIPTION = (
    "Per-channel catch-up of your chat workspace since your last run: mentions of you and decisions first "
    "(verbatim quotes, who, links), then up to 3 highlights per channel, with totals and coverage notes. "
    "Read-only."
)
SKILL_TAGS = ["chat", "catch-up", "mentions", "decisions", "channels", "digest", "read-only"]
SKILL_EXAMPLES = [
    "Catch me up",
    "What did I miss in #payments?",
    "Only decisions since Monday",
]
