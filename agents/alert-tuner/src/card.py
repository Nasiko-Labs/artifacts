"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

Workflow generation sees only the name and description, so the description carries what sets this
agent apart."""

AGENT_NAME = "alert-tuner"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds noisy alerts from your incident pager's history and proposes how to quiet them. It counts, per alert, "
    "how often it fired in the last N days and how many of those incidents nobody acknowledged, annotated, "
    "escalated or resolved by hand, then proposes a longer `for` duration computed from how fast they "
    "self-resolve (\"would have suppressed 20 of 21 firings\"), low urgency for alerts nobody ever acknowledges, "
    "or a threshold change when you paste the metric values at firing. Every count, duration and percentage is "
    "computed in code. On request it opens a pull request on your alert-config repository that changes only the "
    "located `for:` line, with one approval card per write (branch, commit, pull request). It never edits the "
    "pager, silences or deletes alerts."
)

SKILL_ID = "alert-noise-tuning"
SKILL_NAME = "Alert noise tuning"
SKILL_DESCRIPTION = (
    "Noisy alerts from incident history (fired N+ times, 80%+ with no human action), with per-alert counts, "
    "self-resolve durations and computed proposals (longer `for`, low urgency, threshold from pasted values, "
    "review for removal); then a pull request changing only the `for:` line, each write approved on its own card."
)
SKILL_TAGS = ["alerts", "alert fatigue", "on-call", "incidents", "sre", "monitoring", "pull request"]
SKILL_EXAMPLES = [
    "Which alerts are noisy in the last 30 days?",
    "Alerts for service payments that fired 3+ times with no action",
    "Open the PR for the first proposal in acme/infra",
]
