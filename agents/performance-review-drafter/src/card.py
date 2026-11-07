"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "performance-review-drafter"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Drafts a performance review for one person from the examples and feedback a manager pastes into the chat. "
    "Every strength, growth area and summary line cites the evidence items it rests on ([e3, e8]); a claim with "
    "no cited evidence is dropped in code, generic praise or criticism is rejected, and loaded trait words, "
    "personal characteristics, recency skew and one-sided evidence are flagged. It lists evidence gaps (the "
    "competencies with too little evidence to write anything fair) with a question to fill each. It never "
    "assigns a rating and never advises on promotion or pay; the result is a draft for the manager to edit. "
    "Follow-ups: show the evidence, only the gaps, shorter, remove e5, add an example, as json. It reads only "
    "what you paste: no HR system, no mail or chat."
)

SKILL_ID = "draft-performance-review"
SKILL_NAME = "Draft a performance review"
SKILL_DESCRIPTION = (
    "An evidence-cited review draft (summary, strengths, growth areas) from pasted examples, with evidence "
    "gaps and bias flags; no rating."
)
SKILL_TAGS = ["performance review", "feedback", "evidence", "engineering manager", "bias check", "evidence gaps"]
SKILL_EXAMPLES = [
    "Draft a review for Sam from these examples: <paste 5 or more examples>",
    "What evidence am I missing?",
    "Show the evidence behind the strengths",
]
