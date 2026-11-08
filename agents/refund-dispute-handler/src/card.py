"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "refund-dispute-handler"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Handles a refund request or a dispute (chargeback) against your own payments account: given a customer, "
    "a charge or dispute id, or a pasted customer email, it reads the charge, the customer's plan, invoices, "
    "prior refunds and disputes, and your email with them, applies the refund policy in code (14-day window, "
    "auto-approve up to 100.00, prorated annual plans by default, or your organization's refund policy) and "
    "returns an approve / partial / deny / needs-human recommendation with the reasons and evidence. For a "
    "dispute it gives the deadline, an evidence checklist and a packet to paste. It drafts the reply to the "
    "customer and sends it only after you approve that exact text. It never issues a refund, submits dispute "
    "evidence or closes a dispute: you do that in your payments dashboard."
)

SKILL_ID = "refund-dispute"
SKILL_NAME = "Refund and dispute handling"
SKILL_DESCRIPTION = (
    "A refund request or dispute: the charge, customer history and email checked, the refund policy applied in "
    "code, an approve / partial / deny / needs-human recommendation with evidence, a dispute evidence packet, "
    "and a reply draft sent only after you approve the exact text. Never refunds."
)
SKILL_TAGS = ["refunds", "disputes", "chargebacks", "refund policy", "payments", "finance", "support"]
SKILL_EXAMPLES = [
    "Jane wants a refund for last month's charge",
    "What should I do about dispute dp_...?",
    "List my open disputes",
]
