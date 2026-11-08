"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "commitment-advisor"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Advises how much cloud commitment (savings plans, reserved instances) to buy and for how long, from hourly "
    "or daily usage you paste as CSV (timestamp or date, scope or instance_family, region, on_demand_cost) plus, "
    "optionally, your existing commitments after a line '--- commitments'. In code it sizes the hourly commit "
    "on a percentile grid at 95% minimum utilization, shows net savings per month and per term, utilization, "
    "break-even, coverage and a -20% usage downside with a risk class, compares 1 and 3 years, and says which "
    "existing commitments to renew more, renew the same, renew less or let lapse. Discounts are labelled "
    "assumptions (1y 25%, 3y 45%) until you give real quotes. Follow-ups: what if our 1y discount is 30%, should "
    "we go 3 years, min utilization 90%, compute only, as json. It purchases nothing and connects to no account."
)

SKILL_ID = "commitment-advice"
SKILL_NAME = "Commitment advice"
SKILL_DESCRIPTION = (
    "Recommends savings plan / reserved instance purchases and renewals from pasted cloud usage, with the "
    "savings, utilization, break-even and downside risk computed in code."
)
SKILL_TAGS = ["finops", "cloud cost", "savings plans", "reserved instances", "commitments", "rightsizing"]
SKILL_EXAMPLES = [
    "Should we buy a savings plan? <paste usage>",
    "Which commitments should we let lapse?",
    "What if our discount is 30%?",
]
