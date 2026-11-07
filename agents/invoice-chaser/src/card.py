"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "invoice-chaser"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Lists your open customer invoices that are past their due date from your payments account, checks your "
    "own sent email for reminders already sent about each one (matched by invoice number or link), and places "
    "each on a fixed escalation ladder: friendly reminder (1-6 days late), firm reminder (7-13), final notice "
    "(14-29), escalate to a human (30+), raised by earlier reminders and large amounts, with a 3-day cooldown "
    "after a reminder. Ranks them by days late and amount with per-currency totals. On request it drafts the "
    "next reminder at the right level, using only the checked amount, due date and payment link, and sends it "
    "only after you approve that exact text, one email at a time. Never changes an invoice, never charges, "
    "never adds fees or threats. Not for failed card payments on auto-charge invoices."
)

SKILL_ID = "invoice-chase"
SKILL_NAME = "Chase overdue invoices"
SKILL_DESCRIPTION = (
    "Your overdue invoices with days late, reminders already sent, ladder level and next step, ranked, with "
    "per-currency totals. Then the next reminder per invoice, sent only after you approve the exact text."
)
SKILL_TAGS = ["invoices", "overdue", "accounts receivable", "dunning", "payment reminder", "collections", "finance"]
SKILL_EXAMPLES = [
    "Which invoices are overdue?",
    "Chase everything over 14 days late",
    "Draft the next reminder for Globex",
]
