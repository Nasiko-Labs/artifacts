"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "iac-cost-gate"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Estimates the monthly AWS cost of Terraform HCL, a terraform show -json plan or a CloudFormation "
    "template you paste, flags waste (gp2 volumes, unattached IPs and volumes, Multi-AZ or oversized "
    "instances outside prod, log groups that never expire, missing cost tags and more) and returns a "
    "pass, warn or fail verdict against an optional monthly limit. Every number is computed in code from "
    "a static on-demand price table (as of 2026-11, estimate +/-30%) and every assumption is listed. A plan "
    "JSON gets a before/after delta. Follow-ups: only the violations, cost table, set limit $300, assume "
    "500 GB through NAT, region eu-west-1, as json. It never needs credentials, never scans an account and "
    "never blocks anything itself; it answers nothing outside the pasted infrastructure code."
)

SKILL_ID = "iac-cost-check"
SKILL_NAME = "IaC cost check"
SKILL_DESCRIPTION = (
    "Monthly cost estimate, waste-rule violations and a pass/warn/fail verdict for pasted Terraform, "
    "plan JSON or CloudFormation, with every assumption listed."
)
SKILL_TAGS = ["finops", "terraform", "cloudformation", "cost estimate", "waste", "ci gate", "aws"]
SKILL_EXAMPLES = [
    "Cost check this Terraform, limit $500: <paste the .tf files>",
    "Estimate the delta of this plan: <paste terraform show -json output>",
    "Only the violations.",
]
