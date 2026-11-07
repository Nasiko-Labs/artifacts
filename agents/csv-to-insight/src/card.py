"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "csv-to-insight"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Turns a CSV table you paste into the chat (sales, signups, tickets, any table with a header row) into key "
    "findings, one chart and data-quality caveats. The model only picks analyses from a fixed menu (summary "
    "stats, top N, composition, trend over time, group comparison, correlation, outliers, missing values, "
    "duplicates, distribution, cross tab); code runs them with pandas, so every number is computed, never "
    "guessed, and no model-written code ever runs. Findings are checked against the computed facts and never "
    "claim causes. The chart comes as text (sparkline or bars) and, on request, as a PNG for API clients. "
    "Follow-ups work on the same table: chart only, findings only, show the table for r2, as json, also show X "
    "by Y, drill into a value. Up to 200,000 rows. It reads only what you give it: no files, databases or web, "
    "and no general questions."
)

SKILL_ID = "csv-insight"
SKILL_NAME = "CSV to insight"
SKILL_DESCRIPTION = (
    "Key findings, one chart and caveats from a pasted CSV table, every number computed in code from a fixed "
    "menu of analyses, plus follow-ups on the same table."
)
SKILL_TAGS = ["csv", "data analysis", "insights", "chart", "trend", "statistics", "data quality"]
SKILL_EXAMPLES = [
    "What stands out in this data? <paste the CSV>",
    "Revenue by region over time: <paste the CSV>",
    "Show the chart as a PNG.",
]
