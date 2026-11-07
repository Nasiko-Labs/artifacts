"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "alert-noise-classifier"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Classifies a list of fired alerts you paste into the chat (an incident-tool CSV export, Alertmanager or "
    "Grafana webhook JSON, an alert-bot chat paste, or plain lines with a time and an alert name) as actionable, "
    "noise or unclear. Each verdict carries a reason built from counts and durations computed in code: fires "
    "auto-resolved in under 5 minutes, acknowledged with no action, flapping, periodic, or a storm child of "
    "another alert. Weak evidence goes to unclear, never to noise. It then suggests tuning (a for: value, routing "
    "to a ticket, inhibiting, retiring) with its assumption and how many pages it would have saved. Follow-ups: "
    "only noise, only <alert>, why is <alert> noise, show the numbers, top 3 to fix, what if for = 10m, as json. "
    "It judges what fired, not the rule definitions; it reads only what you paste, watches nothing and changes "
    "nothing; no general questions."
)

SKILL_ID = "classify-alert-noise"
SKILL_NAME = "Classify alert noise"
SKILL_DESCRIPTION = (
    "Actionable, noise and unclear alerts from a pasted list of fired alerts, with code-computed reasons, tuning "
    "suggestions with assumptions and pages saved, plus follow-ups on the same classification."
)
SKILL_TAGS = ["alerts", "alert noise", "on-call", "paging", "sre", "alert tuning", "alert fatigue"]
SKILL_EXAMPLES = [
    "Which of these alerts are noise? <paste the alerts>",
    "Classify last week's pages: <paste the export>",
    "Top 3 to fix",
]
