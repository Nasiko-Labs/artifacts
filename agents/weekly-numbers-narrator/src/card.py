"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "weekly-numbers-narrator"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reads your weekly metrics spreadsheet (or a pasted table), computes in code what changed in the latest "
    "period versus the previous one (or the same week last month), picks the metrics that really moved, and "
    "writes a short 'what happened and why' paragraph in which every number is inserted by code. The 'why' "
    "comes only from the sheet's own notes column or context you type; without it the paragraph says the cause "
    "is not in the data. After you approve the exact text, it posts the paragraph to one chat channel. It never "
    "invents a cause or a number and never posts without approval."
)

SKILL_ID = "weekly-numbers-narrative"
SKILL_NAME = "Weekly numbers narrative"
SKILL_DESCRIPTION = (
    "Week-over-week (or vs last month) changes in a metrics sheet, computed in code: movers above a 5% "
    "materiality bar with a noise check, flat metrics, and a checked paragraph whose numbers are code-inserted "
    "and whose causes come only from the sheet's notes or your context. Posts it to a chat channel after you "
    "approve it."
)
SKILL_TAGS = ["weekly metrics", "kpi", "narrative", "week over week", "data analyst", "reporting"]
SKILL_EXAMPLES = [
    "Narrate this week's numbers from KPI sheet",
    "Post it to #leadership",
    "Same but vs last month",
]
