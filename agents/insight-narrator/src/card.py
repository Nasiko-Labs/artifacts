"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "insight-narrator"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Explains a small table in plain English for people who don't read tables. Paste a table (markdown, CSV, "
    "TSV or a JSON array, up to 200 rows) or the numbers behind a chart, optionally with who it's for and how "
    "long; it writes a headline sentence, two or three supporting points and one thing to watch out for. Code "
    "computes every fact (changes, highs and lows, leaders and shares, biggest movers, values far from the rest, "
    "data-quality issues) and checks every number the narrative uses against them; it never states a cause you "
    "didn't give, never uses analyst jargon and never forecasts. Follow-ups on the same table: one sentence, "
    "longer, for execs, simpler, show the numbers, as json, or a question about the table. It cannot read chart "
    "images and does no analysis beyond narration (use csv-to-insight for analysis); no files, databases or web, "
    "and no general questions."
)

SKILL_ID = "narrate-table"
SKILL_NAME = "Insight narrator"
SKILL_DESCRIPTION = (
    "Plain-English story of a pasted table or chart data: a headline, supporting points and one watch-out, every "
    "number computed in code and checked, no jargon and no invented causes."
)
SKILL_TAGS = ["data storytelling", "narrative", "plain english", "table", "chart", "summary", "non-analysts"]
SKILL_EXAMPLES = [
    "Explain this for the sales team: <paste the table>",
    "One sentence on this table: <paste the table>",
    "For execs, no jargon: <paste the table>",
]
