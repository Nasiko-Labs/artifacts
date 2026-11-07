"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "config-explainer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Explains a pasted regular expression, cron schedule, AWS IAM policy or nginx configuration in plain "
    "English and lists its gotchas. The facts are computed in code, not guessed: a cron's next run times and "
    "how often it fires, which test strings a regex matches (run for real), what each IAM statement allows "
    "and the risky permissions, and which nginx location handles a URL and where proxy_pass sends it. Ask "
    "follow-ups like \"next 10 runs in America/New_York\", \"test: abc, a1\", \"can it read "
    "arn:aws:s3:::logs/a.txt?\" or \"what handles /api/v1/users?\". It never applies, deploys or edits a config "
    "and never declares one \"secure\". Not for writing new configs, Kubernetes/Terraform/Docker files, "
    "debugging a running system or general programming questions."
)

SKILL_ID = "explain-config"
SKILL_NAME = "Explain a config"
SKILL_DESCRIPTION = (
    "Plain-English explanation and gotchas for a pasted regex, cron schedule, AWS IAM policy or nginx config, "
    "with computed facts: cron run times, regex test results, IAM 'can it do X?' answers, nginx location and "
    "upstream resolution. Read-only."
)
SKILL_TAGS = ["regex", "cron", "iam", "aws", "nginx", "config", "explain", "developer"]
SKILL_EXAMPLES = [
    "Explain `0 9 13 * 5`",
    "What does this regex do? `^(?:\\d{3}-){2}\\d{4}$` test: 555-123-4567, 5551234567",
    "Explain this IAM policy: {\"Version\": \"2012-10-17\", \"Statement\": [...]}",
    "Which location handles /api/v1/users? server { location /api/ { proxy_pass http://app; } }",
]
