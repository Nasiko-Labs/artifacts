"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "variance-narrator"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Writes the budget-versus-actual variance explanation finance asks for, from a table you paste into the chat "
    "(spreadsheet cells, CSV or a markdown table with budget and actual per line, optionally a driver/comment "
    "column). Every variance, percentage, ranking, materiality call, offset and total is computed in code; the "
    "paragraph gives the headline, the biggest variances (favourable or unfavourable), what offset them and what "
    "is still unexplained. Causes come only from drivers you supplied in the table or your message; anything else "
    "reads 'driver not provided'. Follow-ups: board version, shorter, only unfavourable, threshold 5%, table only, "
    "add driver, as json. It never forecasts, never edits your table and does not answer general questions."
)

SKILL_ID = "variance-narrative"
SKILL_NAME = "Explain budget vs actual"
SKILL_DESCRIPTION = (
    "The variance paragraph for a pasted budget vs actual table: headline, biggest variances with direction, "
    "offsets and what is still unexplained, with every figure computed in code and only your drivers used."
)
SKILL_TAGS = ["variance", "budget vs actual", "FP&A", "finance", "narrative", "month-end", "board"]
SKILL_EXAMPLES = [
    "Explain this variance for the board, Q3 2026: <paste the budget vs actual table>",
    "Two-sentence version",
    "Only unfavourable lines",
]
