"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "billing-export-analyzer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Analyzes a cloud billing / cost export you paste into the chat (CSV with a header row: date, service, "
    "cost, plus account, tags, region or usage type if present; AWS, GCP and Azure exports are recognised). "
    "It returns the period and totals (gross, credits, net), the top movers window over window with "
    "code-computed drivers worded as hypotheses, anomalous days (more than 2 sigma off the trailing mean), "
    "the untagged share, waste patterns with stated assumptions, a budget run-rate, a finance-readable "
    "narrative and a text chart. Every number is computed in code. Follow-ups: only movers, only "
    "anomalies, untagged, chart, top 10 movers, compare last 14 days, cost of a service in a region, as "
    "json. It never connects to a cloud account and changes nothing; no general questions."
)

SKILL_ID = "analyze-billing-export"
SKILL_NAME = "Analyze a billing export"
SKILL_DESCRIPTION = (
    "Totals, top movers with hypotheses, anomalies, untagged share, waste patterns, budget run-rate, a "
    "narrative and a text chart from a pasted cloud cost CSV, plus follow-ups on the same analysis."
)
SKILL_TAGS = ["finops", "cloud cost", "billing", "anomalies", "top movers", "untagged spend", "budget"]
SKILL_EXAMPLES = [
    "Analyze this billing export, budget $40k: <paste the CSV>",
    "Top movers vs last week",
    "How much is untagged?",
]
