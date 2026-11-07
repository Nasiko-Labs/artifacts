"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "spec-to-issues-breaker"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reads a product spec or PRD (a document found by title or link, or text you paste after \"Spec:\") and "
    "proposes a tracker project with issues: an imperative title, a short description, acceptance criteria "
    "quoted word for word from the spec, an estimate computed from the work involved, and dependencies. You "
    "edit the list (drop, merge, retitle, re-estimate), pick which issues to create, and approve each one on "
    "its own card; nothing is created before that. It skips issues that already exist in the team and never "
    "adds a requirement the spec does not state. Not a spec writer and not a general project-management "
    "assistant."
)

SKILL_ID = "spec-to-tracker-issues"
SKILL_NAME = "Break a spec into tracker issues"
SKILL_DESCRIPTION = (
    "Splits a spec into issues with verbatim acceptance criteria, estimates and dependencies, checks the team "
    "for duplicates, and creates the issues you pick, one approval card per issue."
)
SKILL_TAGS = ["spec", "PRD", "issues", "backlog", "estimates", "tracker", "project planning"]
SKILL_EXAMPLES = [
    "Break down the SSO rollout PRD into tracker issues for team Platform.",
    "Turn this spec into a project for team Platform. Spec: <paste the spec here>",
    "Which parts of the spec are unclear?",
]
