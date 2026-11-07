"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "token-spend-analyzer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Analyzes an AI/LLM usage or spend export you paste into the chat: an OpenAI or Anthropic usage CSV, "
    "LiteLLM-style gateway spend logs (JSON/JSONL or CSV), any table with model, token or cost columns, or "
    "Nasiko's own FinOps dashboard JSON (reported by agent). It returns cost by model, by team and by task with "
    "shares, unit economics and cache share, plus waste findings with stated assumptions: retries and failed "
    "calls, oversized prompts, cache underuse, premium models on small calls, output-heavy calls, runaway "
    "teams or spike days, a long tail of models and unpriced usage. Costs missing from the export are computed "
    "from a built-in price table and labelled. Every number is computed in code. Follow-ups: by model, by team, "
    "by task, waste only, top 5, cost of team X, what if gpt-4o-mini replaced gpt-4o for the small calls, as "
    "json. It never contacts a provider and never changes a budget or key; no general questions."
)

SKILL_ID = "analyze-token-spend"
SKILL_NAME = "Analyze LLM token spend"
SKILL_DESCRIPTION = (
    "Cost by model, team and task, and waste findings (retries, oversized prompts, cache misses, model overkill) "
    "from a pasted LLM usage export, plus code-computed follow-ups and what-ifs on the same analysis."
)
SKILL_TAGS = ["finops", "llm cost", "token spend", "ai usage", "waste", "model costs", "cache"]
SKILL_EXAMPLES = [
    "Analyze this OpenAI usage export: <paste the CSV>",
    "Where are we wasting tokens?",
    "By team",
]
