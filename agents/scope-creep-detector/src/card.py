"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "scope-creep-detector"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Shows how a team's sprint (or cycle) scope changed after it started: it compares what was committed at "
    "the start with what is in the sprint now, from the issue tracker's own history. It lists every issue "
    "added after the start with who added it, when and its estimate, what was removed, a by-person and "
    "by-day breakdown of additions, and the net scope change in points (counts when nothing is estimated). "
    "Additions within 24 hours of the start count as planning, not creep (you can change or remove that "
    "grace period). Dates and numbers are computed in code; every issue links to the tracker. Read-only: it "
    "never writes, comments or messages anyone, and it never says whether a change was right. Without a "
    "tracker connection it works on a pasted issue list with dates."
)

SKILL_ID = "scope-creep-detector"
SKILL_NAME = "Sprint scope change"
SKILL_DESCRIPTION = (
    "Committed-at-start vs now for the current (or a named) sprint: issues added after the start with who "
    "and when, removed issues, net points change with a 10/25 % bucket, late additions and likely swaps. "
    "Read-only; no judgments."
)
SKILL_TAGS = ["sprint", "scope creep", "engineering manager", "cycle", "planning", "issues", "read-only"]
SKILL_EXAMPLES = [
    "What got added to the current sprint after we started?",
    "Scope creep in the backend sprint, no grace period",
    "Compare with last sprint",
]
