"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "enterprise-deal-reconciler"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reconciles an enterprise contract or order form with its billing setup before the first invoice goes "
    "out. Reads the contract from your documents (or pasted text), extracts the commercial terms with a "
    "verbatim quote for each (seats, unit price, discount, currency, start and end dates, term, billing "
    "frequency, payment terms, auto-renewal), reads the customer's subscription, schedule, prices and coupons "
    "from your payments account, and compares them field by field in code with fixed tolerances. Returns "
    "mismatches[] with severity (critical, high, medium), the computed delta and the exact fix to make, plus "
    "what was verified, what the contract does not state, and a verdict (clean, review, blocked). Read-only: "
    "never changes billing and writes nothing anywhere."
)

SKILL_ID = "deal-reconcile"
SKILL_NAME = "Reconcile a contract with billing"
SKILL_DESCRIPTION = (
    "Contract terms vs the customer's billing setup, field by field: mismatches with severity, delta and the "
    "exact fix, what was verified, what the contract leaves unstated, and a clean / review / blocked verdict."
)
SKILL_TAGS = ["contract", "order form", "billing", "subscription", "reconciliation", "revenue", "finance"]
SKILL_EXAMPLES = [
    "Reconcile the Globex contract with billing",
    "Check the Acme order form before the first invoice",
    "Here are the contract terms: ...",
]
