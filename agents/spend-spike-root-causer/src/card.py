"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "spend-spike-root-causer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Takes your cloud billing export (pasted CSV: date, service, cost, plus optional usage type, region, account "
    "and tag columns), finds the cost jump in code (last 7 days vs the 28 before, +20% and 500.00 a month if "
    "sustained), and traces it from service to usage type to tag to team. It then looks for merged pull requests, "
    "releases and workflow runs in the owning team's repositories near the onset, finds the owner in chat, and "
    "drafts a message asking them to confirm. Every causal link is a hypothesis with evidence: a deploy is "
    "'coincident with the onset', never 'the cause'. It never changes cloud resources and posts nothing until you "
    "approve the exact text, one post per approval. Not a forecast, a commitment or savings-plan advisor, or a "
    "live cost reader."
)

SKILL_ID = "spend-spike"
SKILL_NAME = "Spend spike root cause"
SKILL_DESCRIPTION = (
    "From a pasted billing export: the spike (per-day before/after, % change, monthly impact, onset date), the "
    "chain service -> usage dimension -> tag -> team, code changes coincident with the onset (ranked in code), "
    "the owner, and a drafted message posted only after you approve the exact text."
)
SKILL_TAGS = ["finops", "cloud cost", "spend spike", "billing export", "root cause", "deploys", "owner"]
SKILL_EXAMPLES = [
    "Why did our cloud bill jump? <paste your billing CSV>",
    "Which team owns the spike?",
    "Draft a message to the owner",
]
