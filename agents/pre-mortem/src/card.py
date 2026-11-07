"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "pre-mortem"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Runs a pre-mortem on a plan, PRD or brief you paste into the chat: it assumes the plan already failed and "
    "writes the 5-7 most likely failure stories, each tied to a specific line of your plan (the assumption it "
    "breaks), with likelihood, impact, an early-warning sign and a mitigation, ranked by likelihood x impact in "
    "code. Four independent lenses (premise, delivery, downside, design) look at the plan separately; a story "
    "that can't quote a line of your plan is dropped as generic. Also lists the unstated assumptions and "
    "questions for the team. Follow-ups: only the top 3, go deeper on one, show the plan lines, as json. It "
    "reads only what you paste, never invents team facts and sends nothing. Not for general risk lists or "
    "writing the plan."
)

SKILL_ID = "pre-mortem"
SKILL_NAME = "Pre-mortem a plan"
SKILL_DESCRIPTION = (
    "The 5-7 most likely ways the pasted plan fails, each anchored on a plan line, ranked by likelihood x "
    "impact, with early warnings, mitigations, unstated assumptions and questions for the team."
)
SKILL_TAGS = ["pre-mortem", "risk", "plan review", "PRD", "failure modes", "assumptions", "launch"]
SKILL_EXAMPLES = [
    "Pre-mortem this plan: <paste the plan>",
    "Pre-mortem this PRD, team of 4, launch Dec 15: <paste the PRD>",
    "Go deeper on failure 2",
]
