"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description. The policy defaults are shown here so the reviewer knows what "dormant" means."""

AGENT_NAME = "access-review"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews who has prod or admin access against your org roster. Paste an access export from your identity "
    "provider (CSV/TSV: user, email, system, role, last sign-in, status) and your roster (email, name, title, "
    "manager, status), and optionally name your code-host organization to check its members, owners, outside "
    "collaborators and repo admins. It flags terminated people who still have access, accounts not on the roster, "
    "dormant accounts, privileged roles without a matching job title, too many owners and outside collaborators "
    "with admin, each with its evidence and rule, and drafts removal requests as text. Defaults: dormant after 90 "
    "days, at most 3 owners, admin titles engineering/SRE/DevOps/platform/security/infrastructure/IT; change them "
    "in one line. It never removes, suspends or changes any access and never sends the requests."
)

SKILL_ID = "access-review"
SKILL_NAME = "Access review"
SKILL_DESCRIPTION = (
    "Compares a pasted access export and roster (plus, optionally, your code-host organization) and lists what "
    "needs a decision: terminated with access, not on roster, dormant, privileged without a role need, excess "
    "owners, outside collaborator admins. Removal requests are drafted as text, never sent."
)
SKILL_TAGS = ["access review", "offboarding", "least privilege", "admin access", "dormant accounts", "audit",
              "security"]
SKILL_EXAMPLES = [
    "Review prod access against this roster (paste `access:` and `roster:` blocks)",
    "Who still has access to our code-host org acme but left the company?",
    "Draft removal requests for terminated people",
]
