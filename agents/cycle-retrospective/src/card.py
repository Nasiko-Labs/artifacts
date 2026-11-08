"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "cycle-retrospective"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Builds a retrospective for one tracker cycle (the last finished one, the current one, or a cycle you name): "
    "planned vs completed, work added mid-cycle and what spilled into the next cycle, with every number computed "
    "in code. For each spilled issue it looks for the stated reason in the issue's comments and in chat, and "
    "shows a reason only with a verbatim quote; otherwise it says no reason was found. It never blames a "
    "person. It ends with discussion prompts picked by fixed rules, and posts the retro to a chat channel only "
    "when you name one and approve that exact text. Not a sprint planner, not a status report and not a "
    "per-person performance tool."
)

SKILL_ID = "cycle-retrospective"
SKILL_NAME = "Cycle retrospective"
SKILL_DESCRIPTION = (
    "For one tracker cycle: planned vs completed, scope added mid-cycle, spilled work with evidence-backed reasons "
    "(a verbatim quote or no reason found), shipped list and discussion prompts; optionally posted to a chat "
    "channel you name, after you approve it."
)
SKILL_TAGS = ["retrospective", "retro", "cycle", "sprint", "spillover", "scope creep", "tracker", "chat"]
SKILL_EXAMPLES = [
    "Retro for the last cycle",
    "Why did work spill in cycle 24 for Platform?",
    "Retro for the current cycle and post it to #eng-retro",
]
