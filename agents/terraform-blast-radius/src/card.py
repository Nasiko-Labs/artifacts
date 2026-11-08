"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "terraform-blast-radius"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Shows the blast radius of a Terraform plan you paste (the text of terraform plan or terraform show -json) "
    "plus the repository it belongs to: it lists what the plan changes, scores the risk of each change from a "
    "visible table (destroy 5 + stateful 3 ...), then searches your code host for what depends on the changed "
    "resources: module consumers, stacks reading their state, and services or manifests that reference their "
    "names or hosts, each with path:line, environment and owner (CODEOWNERS or recent committers). Returns "
    "affected[] ranked by risk, prod first. Follow-ups: only destroys and replacements, who owns a module, why "
    "a change is high, search another repository, as json. Read-only: it never runs Terraform, never comments "
    "and never writes anywhere. Not for policy checks or cost estimates."
)

SKILL_ID = "terraform-blast-radius"
SKILL_NAME = "Terraform blast radius"
SKILL_DESCRIPTION = (
    "What a pasted Terraform plan changes, the risk of each change (scored in code), and what depends on the "
    "changed resources in your code host (module consumers, state readers, services referencing them), ranked "
    "by risk and environment, with owners."
)
SKILL_TAGS = ["terraform", "infrastructure as code", "blast radius", "change risk", "dependencies", "sre",
              "platform"]
SKILL_EXAMPLES = [
    "Here is my terraform plan for acme/infra: what could it break? <paste the plan>",
    "Who owns what this plan changes?",
    "Only show destroys and replacements",
]
