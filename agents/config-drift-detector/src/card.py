"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "config-drift-detector"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Finds configuration drift: where live infrastructure no longer matches its infrastructure-as-code. Paste "
    "the output of `terraform plan -refresh-only` (with the repository), or name an IaC file in the code host "
    "and paste the live object (`kubectl get ... -o yaml`, `terraform state show`, a JSON export). It lists "
    "every drifted attribute with expected vs actual, severity, the IaC file and line, who last changed the "
    "file in code and, when you paste audit events, who changed it by hand. Values are compared in code, "
    "never guessed; secrets are redacted. Read-only: it never applies, reverts, edits or opens pull requests."
)

SKILL_ID = "config-drift"
SKILL_NAME = "Config drift"
SKILL_DESCRIPTION = (
    "Compares infrastructure code with pasted live state and returns drift[]: resource, attribute, expected, "
    "actual, severity, file:line, last code author and the manual actor from pasted audit events."
)
SKILL_TAGS = ["drift", "terraform", "kubernetes", "infrastructure as code", "sre", "audit", "platform"]
SKILL_EXAMPLES = [
    "Here is `terraform plan -refresh-only` for acme/infra: what drifted?",
    "Compare k8s/payments.yaml in acme/infra to this live YAML",
    "Who changed the security group? (audit events pasted)",
]
