"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "prd-writer-stateless"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns a product brief you paste into the chat (the idea, who it is for, constraints, any data) into a "
    "PRD with fixed sections: problem, users, goals, non-goals, requirements (with priority and an "
    "acceptance check), success metrics and open questions. It writes only from your brief: every "
    "statement rests on facts quoted from it, anything it proposes is marked (proposed), numbers are never "
    "invented (a target the brief does not give becomes [target?]) and every gap becomes an open question. "
    "A small CrewAI crew does the writing: a fact extractor quotes your brief, a PRD author drafts from those "
    "facts, and an editor strikes anything the facts don't support. "
    "Revise by instruction (make requirement 3 testable, add a non-goal about mobile), or ask for only the "
    "open questions, show sources, shorter, as json. Stateless: it keeps nothing between messages, so "
    "follow-ups and revisions work on what is still in the recent conversation (a revision starts from the "
    "last PRD it showed you; other follow-ups from your brief, else that draft), and a very long earlier brief "
    "may need re-pasting. It does not research, estimate, write stories or "
    "grade an existing PRD, and it answers no general questions."
)

SKILL_ID = "write-prd"
SKILL_NAME = "Write a PRD from a brief"
SKILL_DESCRIPTION = (
    "A PRD (problem, users, goals, non-goals, requirements, metrics, open questions) drafted only from the "
    "brief you paste, with proposed content marked and gaps turned into open questions; revise it by "
    "instruction."
)
SKILL_TAGS = ["prd", "product requirements", "product brief", "requirements", "success metrics",
              "open questions", "product management"]
SKILL_EXAMPLES = [
    "Write a PRD from this brief: <paste the brief>",
    "Make requirement 2 testable",
    "What are the open questions?",
]
