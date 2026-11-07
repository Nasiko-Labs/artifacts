"""Card text, shared by __main__.py (the served card), smalltalk.py (the intro) and AgentCard.json
(checked by a test). The description carries the distinction from other agents: workflow generation
sees only the name and description."""

AGENT_NAME = "churn-early-warning"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Ranks which paying customers might churn, on request: it reads your payments data for cancellations, "
    "scheduled cancellations, downgrades, seat cuts, past-due and repeatedly failed payments, trials ending "
    "and renewals in the next 30 days (7 to 90 if you ask) over changes in the last 30 days (7 to 60), then "
    "checks your email (and a support desk, if connected) for a recent complaint quoted word for word or a "
    "customer gone quiet. Every MRR value, date and risk score is computed in code; each account shows its "
    "evidence and a suggested outreach (save call, win-back, billing help, renewal check-in...). It ranks "
    "risk, it never says a customer will churn, and it never changes a subscription. On request it drafts an "
    "outreach email and sends it only after you approve that exact text, one draft at a time. No payments "
    "connection? Paste rows of account, email, mrr, renewal_date, status, changed_at, change."
)

SKILL_ID = "churn-warning"
SKILL_NAME = "Churn early warning"
SKILL_DESCRIPTION = (
    "Accounts at risk of churning in the next 30 days (7-90), ranked by a transparent score from billing "
    "signals, verbatim email complaints, silence and support tickets, with MRR, evidence and a suggested "
    "outreach. Then an outreach email per account, sent only after you approve the exact text."
)
SKILL_TAGS = ["churn", "retention", "renewals", "subscriptions", "MRR", "at risk", "outreach", "finance"]
SKILL_EXAMPLES = [
    "Who might churn this month?",
    "Which renewals in the next 14 days look risky?",
    "Draft outreach for the top two",
]
