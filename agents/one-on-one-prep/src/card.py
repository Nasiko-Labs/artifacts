"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "one-on-one-prep"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Prepares a manager's next 1:1 with one report from the last meeting's notes and the recent updates the "
    "manager pastes into the chat: talking points, open-ended questions and concrete things to recognize. "
    "Commitments from the last notes are checked against the updates (done / in progress / no evidence), the "
    "manager's own unfulfilled commitments are flagged, and every item quotes the notes or updates verbatim. "
    "Personal or HR matters are found in code and left out unless the manager says to include them. "
    "Follow-ups: only the questions, recognize only, add a topic, drop an item, show sources, as json, or ask "
    "what was agreed last time. It never evaluates or compares people, never infers feelings and sends nothing. "
    "Not for performance reviews or general management advice."
)

SKILL_ID = "one-on-one-prep"
SKILL_NAME = "Prepare a 1:1"
SKILL_DESCRIPTION = (
    "Talking points, questions and recognition for the next 1:1, from pasted notes and updates: carry-overs "
    "checked against the updates, the manager's own open commitments flagged, every item quoted."
)
SKILL_TAGS = ["1:1", "one-on-one", "engineering manager", "meeting prep", "talking points", "recognition"]
SKILL_EXAMPLES = [
    "Prep my 1:1 with Priya. Notes: <last meeting's notes> Updates: <what happened since>",
    "Only the questions",
    "What did we agree about on-call?",
]
