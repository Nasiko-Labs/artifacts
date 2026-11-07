"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "terraform-plan-reviewer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews a Terraform plan you paste into the chat (`terraform plan` text, `terraform show -json` or "
    "`terraform plan -json` lines) and returns a verdict (safe, review or block), the counts, the high- and "
    "medium-risk changes, questions to ask the author and a suggested apply order. The plan is parsed in "
    "code: destroys or replaces of databases, buckets, volumes, keys and other stateful resources, silent "
    "data-loss settings (final snapshot skipped, deletion protection off, versioning suspended), downtime, "
    "public exposure and scope creep are found by fixed rules, and a prod destroy of a stateful resource is "
    "blocked until you confirm it. It never runs terraform or applies anything, reads only what you paste, "
    "and answers no general questions."
)

SKILL_ID = "review-terraform-plan"
SKILL_NAME = "Review a Terraform plan"
SKILL_DESCRIPTION = (
    "A safe / review / block verdict on a pasted Terraform plan, with the risky changes, the plan lines "
    "behind them, questions for the author and an apply order; follow-ups work on the same review."
)
SKILL_TAGS = ["terraform", "infrastructure", "plan review", "change review", "sre", "data loss", "iac"]
SKILL_EXAMPLES = [
    "Review this prod plan: <paste terraform plan output>",
    "Staging, intent: add an SQS queue. <paste the plan>",
    "Only the high-risk changes, with evidence.",
]
