"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name and
description."""

AGENT_NAME = "finance-rollups"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Month-end close view from your own payments account (default: the last 3 full calendar months, UTC; also "
    "\"last month\", \"Q1\", \"Jan to Mar 2026\", up to 12 months): gross volume, refunds, disputes, fees, net and "
    "payouts per month and currency from balance transactions, plus invoiced revenue by paid date, month over "
    "month changes and refund and fee rates. Paste ledger totals (for example a profit-and-loss export) and it "
    "ties them out month by month with a 1.00 / 0.5% tolerance, listing each variance with only the explanations "
    "the data supports (invoices paid across a month boundary, refunds, fees or credit notes netted, currency, a "
    "missing side). Every sum, difference and percentage is computed in code. Read-only: it never writes to the "
    "payments account or any ledger."
)

SKILL_ID = "finance-rollup"
SKILL_NAME = "Month-end finance rollup"
SKILL_DESCRIPTION = (
    "Rolls up a month range of payments activity (gross, refunds, disputes, fees, net, payouts, invoiced paid) per "
    "month and currency, computed in code, ties it out against ledger totals you paste with code-derived "
    "candidate explanations for each variance, and writes a short month-end narrative. Read-only."
)
SKILL_TAGS = ["finance", "month-end", "close", "rollup", "reconciliation", "payments"]
SKILL_EXAMPLES = [
    "Run the month-end rollup for March",
    "Roll up Q1 and show refunds and fees",
    "Reconcile it against this ledger: <paste month, account, amount, currency rows>",
]
