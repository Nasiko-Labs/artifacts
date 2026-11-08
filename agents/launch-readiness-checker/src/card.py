"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "launch-readiness-checker"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Checks whether one named launch is ready, across four areas: engineering (open, urgent, blocked and "
    "unowned issues in your tracker), docs (a launch doc that exists, is fresh and has no TODO/TBD markers), "
    "support (a macro or help-center article that mentions the launch) and marketing (launch tasks, overdue "
    "or unowned ones). Every rule runs in code on what the tools returned, with evidence for each line; the "
    "verdict (go, conditional, no-go) is computed in code, never by the model. An area it could not read is "
    "marked unknown and caps the verdict at conditional. Returns checklist[], verdict, blockers[] and "
    "unknown_sources[]. Read-only: never edits a task, doc or macro."
)

SKILL_ID = "launch-readiness"
SKILL_NAME = "Check launch readiness"
SKILL_DESCRIPTION = (
    "Go / conditional / no-go for one named launch: 12 fixed checks across engineering, docs, support and "
    "marketing, each with its evidence and links, plus what blocks the launch and what could not be read."
)
SKILL_TAGS = ["launch", "release", "readiness", "go/no-go", "checklist", "product management"]
SKILL_EXAMPLES = [
    "Is Dashboards 2.0 ready to launch on Nov 20?",
    "Go/no-go for the billing revamp",
    "What is blocking the SSO launch?",
]
