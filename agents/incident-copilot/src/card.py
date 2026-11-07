"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "incident-copilot"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "On-call incident copilot. Name an incident (link or id) or a service that is alerting, and in about two "
    "minutes it builds one incident card: what is broken, when it started, a UTC timeline, the top 3 suspect "
    "deploys or merged PRs ranked in code by how close they landed to the start, whether their changed files "
    "appear in the error stack traces, and when errors began, plus similar past incidents with how they were "
    "fixed and the runbook link. It reads your connected alerting, metrics, logs, error tracking, code host "
    "and documents, says plainly which sources were missing, and never invents a cause. On request it posts "
    "the card to the incident's chat channel, optionally tagging the top suspect's author, only after you "
    "approve that exact card and that exact person. Runs on demand in chat; it never acknowledges, resolves "
    "or rolls back anything."
)

SKILL_ID = "incident-card"
SKILL_NAME = "Incident card"
SKILL_DESCRIPTION = (
    "One incident card for a firing alert: what's broken, start time, timeline, top suspect changes with "
    "evidence, similar past incidents and their fixes, runbook, and what could not be checked. Then, on "
    "approval, posted to the incident's chat channel."
)
SKILL_TAGS = ["incident", "on-call", "alert", "triage", "deploys", "root cause", "postmortem", "runbook"]
SKILL_EXAMPLES = [
    "Investigate the checkout-api alert",
    "What broke in incident INC-4821? It started 20 minutes ago.",
    "Post the card to #inc-4821 and tag the author of the top suspect.",
]
