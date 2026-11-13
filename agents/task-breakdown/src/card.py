"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "task-breakdown"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Turns a goal, spec or messy brief you type or paste into a structured work plan: 3-6 phases named by "
    "outcome, small tasks with ids, relative sizes (S/M/L/XL), dependencies, 1-3 acceptance criteria and owner "
    "placeholders. Code computes the order of work, the parallel waves, the critical path in relative units, "
    "per-phase totals and lanes for a team of N. Also emits a downloadable task_plan.md. Revise it in the same "
    "chat (split T4, merge T2 and T3, add a security review phase, we are 2 people) and ask about it (critical "
    "path?, what depends on T5?) without re-planning. It never invents owners, dates or tools and creates "
    "nothing in a tracker: the plan is a draft. Not for writing the deliverable itself or general questions."
)

SKILL_ID = "task-breakdown"
SKILL_NAME = "Break a goal into a work plan"
SKILL_DESCRIPTION = (
    "Phases, sized tasks with dependencies and acceptance criteria, parallel waves, the critical path and team "
    "lanes computed in code, plus a task_plan.md file; revisable in the same chat."
)
SKILL_TAGS = ["planning", "task breakdown", "work plan", "critical path", "dependencies", "PRD", "project"]
SKILL_EXAMPLES = [
    "Break down building a customer feedback dashboard into tasks",
    "Here's our PRD: <paste>. Make a phased plan",
    "Plan a 3-week migration from X to Y with 2 engineers",
]
