"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "prd-critic-stateless"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Reviews a PRD you paste into the chat and returns its gaps: missing sections (problem, users, goals, "
    "non-goals, requirements, metrics, open questions), vague or untestable requirements, success metrics that "
    "are not measurable (no number, window or baseline), placeholders and unowned open questions, plus the "
    "questions a reviewer will ask, each tied to a quoted line of the PRD. A fixed rubric in code produces a "
    "0-100 score with a band and a line-by-line breakdown, so the same PRD always gets the same score. A small "
    "CrewAI crew flags the requirements and drafts the questions, then a skeptical second pass drops the weak "
    "points. "
    "Follow-ups: only blockers, only metrics, list the questions, show score breakdown, as json. It keeps nothing between messages: follow-ups work on the PRD and review still in the recent conversation, and a long PRD from much earlier may need re-pasting. It reads only "
    "what you paste, never rewrites the PRD, never judges whether the idea is good, and sends nothing. Not for "
    "writing a PRD or general product advice."
)

SKILL_ID = "prd-review"
SKILL_NAME = "Review a PRD"
SKILL_DESCRIPTION = (
    "Gaps, reviewer questions and a 0-100 rubric score for a pasted PRD: missing sections, vague requirements, "
    "unmeasurable metrics, placeholders and unanswered questions, each tied to a quoted PRD line."
)
SKILL_TAGS = ["PRD", "product requirements", "review", "critique", "gaps", "success metrics", "product manager"]
SKILL_EXAMPLES = [
    "Review this PRD: <paste the PRD>",
    "Only the blockers",
    "Which metrics are not measurable?",
]
