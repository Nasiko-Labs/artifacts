"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "decision-memo-writer"
AGENT_VERSION = "1.0.1"
AGENT_DESCRIPTION = (
    "Writes a one-page decision memo from the decision, options, constraints, criteria and data you PASTE: "
    "context, an options table with pros and cons, a recommendation labelled as a draft with its basis, risks "
    "with mitigations, and what would change the decision. Weighted scores are computed in code from the "
    "criteria you give (e.g. cost 40%, speed 35%, risk 25%); numbers appear only if you gave them, unknowns "
    "are left as [figure?]. Revise with \"recommend B instead\", \"add option C: ...\", \"change the weights: "
    "cost 60%\", \"shorter\", \"just the recommendation\" or \"as json\". It does not research options, fetch "
    "data, send anything, or answer general questions."
)

SKILL_ID = "decision-memo"
SKILL_NAME = "One-page decision memo"
SKILL_DESCRIPTION = (
    "A one-page decision memo (context, options with pros/cons and code-computed scores, a draft "
    "recommendation with its basis, risks, what would change the decision) from what you paste."
)
SKILL_TAGS = ["decision memo", "options", "recommendation", "trade-offs", "leadership", "one-pager"]
SKILL_EXAMPLES = [
    ("Decision memo: build or buy our billing engine. Options: ... Constraints: ... Criteria: cost 40%, "
     "speed 35%, risk 25%"),
    "Write a memo for my VP on whether to delay the launch",
    "Recommend option B instead",
]
