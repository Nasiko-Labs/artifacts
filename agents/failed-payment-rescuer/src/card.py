"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "failed-payment-rescuer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds failed payments in your own payments account over the last 7 days (1 to 30 if you ask, or since "
    "your last run in this conversation): failed charges and invoices whose latest attempt failed. For each "
    "it reads the customer's plan and payment history, classifies the decline reason and how recoverable it "
    "is, flags high-value customers, and ranks them by recoverability, value and how soon retries run out. "
    "On request it drafts a personalised recovery email per failure and a chat digest of the high-value ones, "
    "and sends each only after you approve that exact text, one at a time. Every customer, amount and code "
    "is checked against the tool output. It never retries a charge, changes a subscription or offers a "
    "discount. On demand only: there is no automatic trigger."
)

SKILL_ID = "failed-payment-rescue"
SKILL_NAME = "Failed payment rescue"
SKILL_DESCRIPTION = (
    "Failed payments in the last 7 days (1-30): customer, plan, amount, decline reason, recoverability, next "
    "retry, high value, ranked. Then a recovery email per failure and a chat digest of the high-value ones, "
    "each sent only after you approve the exact text."
)
SKILL_TAGS = ["failed payments", "dunning", "recovery", "decline", "churn", "billing", "finance"]
SKILL_EXAMPLES = [
    "Show failed payments from the last 7 days",
    "Draft recovery emails for the high-value ones",
    "Post the biggest failures to #finance-alerts",
]
