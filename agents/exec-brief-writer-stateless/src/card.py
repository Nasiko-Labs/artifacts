"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "exec-brief-writer-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns the outputs of other agents (or your own notes) that you PASTE into one conversation into a "
    "one-page executive brief with exactly three parts: a headline, what's at risk, and the decisions "
    "needed. Every line is tagged with the pasted section it came from, and any line it cannot trace to "
    "your pasted text is left out and counted. Paste several outputs separated by --- or headings, add "
    "more later (\"add this too\"), revise (\"shorter headline\", \"drop risk 2\", \"only top 3\"), or "
    "ask to \"show sources\". Stateless: it keeps nothing between messages, so follow-ups work on the brief and "
    "pastes still visible in the recent conversation; very long earlier pastes may need re-pasting. It never "
    "asks back: with nothing pasted it asks for the paste in one line. It does not fetch anything, call other "
    "agents, or answer general questions."
)

SKILL_ID = "exec-brief-stateless"
SKILL_NAME = "One-page exec brief"
SKILL_DESCRIPTION = (
    "A one-page brief (headline, what's at risk, decisions needed) built only from the agent outputs or "
    "notes you paste, each line tagged with its source section; untraceable lines are dropped. Revisable "
    "while the brief and its pastes are still in the recent conversation (or pasted again)."
)
SKILL_TAGS = ["executive brief", "summary", "risks", "decisions", "one-pager", "leadership update"]
SKILL_EXAMPLES = [
    "Brief for the CRO on the Acme renewal: <paste the agent outputs, separated by --->",
    "Add this too: <paste another output>",
    "Show sources",
]
