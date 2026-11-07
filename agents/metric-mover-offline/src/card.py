"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "metric-mover-offline"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Explains why a metric moved from a before/after table you paste into the chat, broken down by segment "
    "(channel, country, platform, plan...). Code computes everything: each segment's contribution, mix shift "
    "versus rate change for conversion-style rates (with counts), how concentrated the change is, new or "
    "vanished segments, a Simpson's-paradox check and two-proportion significance tests. It returns up to six "
    "ranked hypotheses, each backed by computed evidence and a generic way to confirm it, and a "
    "measurement-artifact hypothesis whenever data quality could explain the move. It never states a cause as "
    "fact and never forecasts. Follow-ups work on the same table: by <dimension> only, top 3, show the numbers, "
    "as json, add event: <date> <what happened>, ignore <segment>. Up to 5,000 rows and 4 segment columns. It "
    "reads only what you give it: no warehouse, files or web, and no general questions."
)

SKILL_ID = "explain-metric-move"
SKILL_NAME = "Explain a metric move"
SKILL_DESCRIPTION = (
    "Ranked, evidence-backed hypotheses for why a metric changed, from a pasted before/after table by segment, "
    "with the decomposition computed in code, plus follow-ups on the same table."
)
SKILL_TAGS = ["metrics", "data analysis", "root cause", "conversion rate", "mix shift", "segments", "hypotheses"]
SKILL_EXAMPLES = [
    "Signups dropped; here is before/after by channel and country: <paste the table>",
    "Revenue by plan, last week vs this week: <paste the table>",
    "Add event: Oct 14 deploy of the new checkout",
]
