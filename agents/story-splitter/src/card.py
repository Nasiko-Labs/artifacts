"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "story-splitter"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Splits an epic you paste into the chat into thin vertical user stories (as a / I want / so that), each with "
    "2-5 Given/When/Then acceptance criteria, a rough size (S, M or L) computed in code from reported facts, "
    "and a dependency order whose first story can ship alone. Large stories are split once more automatically. "
    "It never invents actors, business rules, numbers or system names: anything the epic doesn't say becomes a "
    "visible placeholder or an open question, and capabilities no story covers are listed. Honours "
    "'Out of scope:' lines and 'max N stories'. Follow-ups: split story 3, merge 4 and 5, drop 6, which can "
    "ship first, as json. Read only: it creates no tickets and gives no day or point estimates."
)

SKILL_ID = "split-epic"
SKILL_NAME = "Split an epic into user stories"
SKILL_DESCRIPTION = (
    "Ordered, thin vertical user stories from a pasted epic, with Given/When/Then acceptance criteria, S/M/L "
    "sizes computed in code, dependencies, uncovered capabilities and open questions."
)
SKILL_TAGS = ["user stories", "epic", "story splitting", "acceptance criteria", "backlog", "agile", "PM"]
SKILL_EXAMPLES = [
    "Split this epic into stories: <paste the epic>",
    "Split story 3 further",
    "Which stories can ship first?",
]
