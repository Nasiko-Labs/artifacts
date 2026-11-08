"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "metrics-pull"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Pulls fresh numbers from a spreadsheet you name (title or link, optional tab and range) or from a table "
    "you paste, and returns them as a clean table: the columns you ask for, filters, grouping and simple "
    "aggregates (sum, average, min, max, count, latest) and period-over-period comparisons, all computed in "
    "code, with an as-of time and the source range on every answer. Say \"as json\" for a JSON envelope a "
    "workflow step can read. It re-reads the sheet on every request, reports a missing column instead of "
    "guessing, never edits the sheet and never explains why numbers moved."
)

SKILL_ID = "metrics-pull"
SKILL_NAME = "Metrics pull"
SKILL_DESCRIPTION = (
    "Reads a spreadsheet (or a pasted table) and returns the requested columns, filters, groups, totals, latest "
    "values and period-over-period changes as a table with as-of time and source range; JSON on request."
)
SKILL_TAGS = ["metrics", "spreadsheet", "table", "kpi", "data pull", "json"]
SKILL_EXAMPLES = [
    "Latest weekly signups by region from KPI sheet",
    "Total revenue by plan for October",
    "Columns date, mrr, churn from 'Finance Weekly' as json",
]
