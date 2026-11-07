"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "alert-rule-reviewer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews alert configuration you paste into the chat (Prometheus rule files, Alertmanager config, "
    "Datadog monitors as JSON or Terraform, CloudWatch alarms as Terraform or CloudFormation, Grafana-managed "
    "rules) and returns findings with the config line they come from: rules built to flap (no `for`, short "
    "windows, irate, bare counters), duplicate or overlapping alerts, missing thresholds, missing owners and "
    "runbooks, paging on causes (CPU, memory, disk) instead of user symptoms, and routing gaps (no grouping, "
    "orphan receivers, missing inhibit rules). Every rule is checked in code; the model only labels each alert "
    "as symptom or cause. Proposed changes are YAML/HCL snippets with placeholders for owners, runbooks and "
    "thresholds, never guessed values. It sees how rules are written, not how often they fire. Follow-ups: only "
    "the high ones, only one rule id, show the config for an alert, proposed changes only, why a rule matters, "
    "as json. Read-only: it changes nothing."
)

SKILL_ID = "review-alert-rules"
SKILL_NAME = "Review alert rules"
SKILL_DESCRIPTION = (
    "Findings (noise-prone rules, duplicates, missing thresholds, owners and runbooks, cause-based paging, "
    "routing gaps) and proposed config snippets for pasted Prometheus, Alertmanager, Datadog, CloudWatch or "
    "Grafana alert rules."
)
SKILL_TAGS = ["alerting", "prometheus", "alertmanager", "datadog", "cloudwatch", "sre", "on-call", "noise"]
SKILL_EXAMPLES = [
    "Review these alert rules: <paste your rules file>",
    "We page the platform team; check routing: <paste alertmanager.yml and the rules>",
    "Only the high findings.",
]
