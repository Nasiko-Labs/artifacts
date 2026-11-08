"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "runbook-generator"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Writes a step-by-step operational runbook from a service description (what it is, what it depends "
    "on, how it fails) or an incident transcript (what responders actually ran) that you paste into the "
    "chat: when to use it, before you start, read-only triage and diagnosis checks, mitigation ordered "
    "least disruptive first with a rollback for every state-changing step, verification, escalation and "
    "after-incident steps. Every command is classified in code as read-only, state-changing or "
    "destructive; each step says whether it came from your text or is suggested. Hosts, URLs, numbers "
    "and contacts that are not in your text stay visible placeholders to fill in. Follow-ups: checks "
    "only, rollback only, high-risk only, shorter, show sources, fill in <NAME> = value, add a step for "
    "X, as json. It never runs anything and reads only what you paste: no general questions."
)

SKILL_ID = "write-runbook"
SKILL_NAME = "Write a runbook"
SKILL_DESCRIPTION = (
    "An operational runbook (triage, diagnosis, mitigation with rollbacks, verification, escalation) from "
    "a pasted service description or incident transcript, with code-classified command safety, "
    "provenance per step and placeholders for unknowns."
)
SKILL_TAGS = ["runbook", "sre", "on-call", "incident", "playbook", "rollback", "operations"]
SKILL_EXAMPLES = [
    "Runbook for when the orders DB pool is exhausted: <describe the service>",
    "Make a runbook from this incident channel log: <paste the transcript>",
    "Rollback steps only",
]
