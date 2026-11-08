"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "idle-resource-reaper"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews a cloud resource inventory you paste (CSV, or the JSON of the standard describe commands for "
    "volumes, instances, snapshots, addresses, load balancers and NAT gateways) and finds idle resources by "
    "fixed rules: unattached volumes, idle or long-stopped instances, orphaned snapshots, unassociated IPs, "
    "idle load balancers and NAT gateways. Each gets its idle days and a monthly cost computed in code "
    "(your pasted cost, else a dated list-price estimate, labelled), and an owner from its tags or, with "
    "evidence, from the code host; unknown owners are shown, never guessed. Resources tagged keep are left "
    "out and counted. On request it drafts one \"delete or justify\" message per owner with a reply-by "
    "date and sends it by chat or email only after you approve that exact text, one draft at a time. It "
    "never deletes, stops or changes any resource."
)

SKILL_ID = "idle-reaper"
SKILL_NAME = "Idle resource reaper"
SKILL_DESCRIPTION = (
    "Idle cloud resources in a pasted inventory, by fixed rules, with idle days, monthly cost (reported or "
    "a labelled estimate), totals and the owner from tags or code-host evidence. Then one delete-or-justify "
    "message per owner, sent only after you approve the exact text. Never deletes anything."
)
SKILL_TAGS = ["finops", "cloud cost", "idle resources", "unattached volumes", "orphaned snapshots", "cleanup",
              "owners"]
SKILL_EXAMPLES = [
    "Find idle resources in this inventory: <paste CSV or describe-volumes JSON>",
    "Who owns the biggest ones?",
    "Draft messages to the owners",
]
