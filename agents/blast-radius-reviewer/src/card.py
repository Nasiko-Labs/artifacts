"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "blast-radius-reviewer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews a code diff you paste (git diff, PR .patch/.diff or diff -u) for what could break OUTSIDE the diff: "
    "callers of removed or changed functions, other clients reading a removed field, existing rows hit by a "
    "migration, renamed env vars and config keys, a rule that lives in a second place, changed defaults, "
    "dependency bumps, workers that restart separately, tests, dashboards, generated code and deleted files. "
    "Twelve risk probes fire in code; the model only judges each one. For every risk it gives the reason, the "
    "evidence rung, a grep command built from a token in your diff and the one fact the change's safety hangs on, "
    "then a verdict (block, review, or nothing found in what you pasted; never 'safe to merge'). Paste grep "
    "output or callers' code after 'context:' to clear or confirm items. It reads only what you paste, never "
    "invents a caller or file, runs nothing and sends nothing. Not for style review, bugs inside the diff, "
    "writing code or security audits."
)

SKILL_ID = "blast-radius-review"
SKILL_NAME = "What could this diff break?"
SKILL_DESCRIPTION = (
    "Places outside a pasted diff that could break (callers, clients, stored data, config, rules living twice, "
    "defaults, dependencies, rollout, tests, observability, generated code, deletions), each with why, a grep "
    "check and its evidence rung, plus the one safety fact and a block/review/nothing-found verdict."
)
SKILL_TAGS = ["code review", "diff", "blast radius", "breaking change", "migration", "pull request", "regression"]
SKILL_EXAMPLES = [
    "What could break? <paste a git diff>",
    "Blast radius of this change + here's the grep output for `format_date`: <diff> context: <grep output>",
    "Review this migration for what it could break: <paste the migration diff>",
]
