"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "postmortem-follow-through"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds the postmortems from the last 6 months (up to 12 if you ask) in your tracker, and in your "
    "postmortem documents when a document source is connected, checks every action item against the "
    "tracker, and lists the ones that slipped: unassigned, untouched for 30+ days, or closed without any "
    "linked change or comment. Each row shows the owner, the age, the originating incident and whether a "
    "repeat incident already happened. It also answers \"does this new outage match an old postmortem "
    "whose fix was never done?\". On request it nudges owners by chat DM (one message per owner) and "
    "reopens tickets closed without evidence, only after you approve that exact message or reopen. It "
    "refuses rather than guessing when the tracker is not connected. Not an incident responder, and it "
    "never creates, assigns or deletes tickets."
)

SKILL_ID = "postmortem-action-items"
SKILL_NAME = "Postmortem action items"
SKILL_DESCRIPTION = (
    "Slipped postmortem action items from the last 6-12 months: unassigned, untouched 30+ days, or closed "
    "without evidence, with owner, age, originating incident and repeat incidents. Matches a new outage to "
    "old postmortems. Nudges owners by chat or reopens tickets, only after you approve each one."
)
SKILL_TAGS = ["postmortem", "action items", "incident follow-up", "reliability", "tracker", "repeat incidents",
              "accountability"]
SKILL_EXAMPLES = [
    "Which postmortem action items from the last 6 months have slipped?",
    "Does INC-881 match an old postmortem whose fix was never done?",
    "Nudge the owners of #2 and #5.",
]
