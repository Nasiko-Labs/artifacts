"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from the plain security reviewer: workflow generation and the
router see only the name and description."""

AGENT_NAME = "security-gate"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Security gate for one pull request: runs a bundled static-analysis ruleset (SQL and command injection, "
    "eval, unsafe deserialisation, weak hashes and ciphers, disabled TLS checks, path traversal, SSRF, open "
    "redirects, permissive CORS, committed secrets) over the PR's changed files in Python, JavaScript/TypeScript, "
    "Go and Java, and answers whether the PR passes: a verdict (pass, needs attention, fail, incomplete) computed "
    "in code from the findings on changed lines only, each finding with file, line, rule, why it matters and a "
    "suggested fix. Existing issues in touched files are listed but never gate. Secrets are masked. Also scans a "
    "pasted diff or code, or triages pasted scanner findings (JSON or SARIF). After you approve the exact text it "
    "can post one summary comment on the PR. Advice only, not a security audit: it never merges, approves, "
    "requests changes, pushes or blocks a merge, and never runs the PR's code. Use it for a pass/fail security "
    "check of a PR; for a free-form security review of code, use a security reviewer."
)

SKILL_ID = "pr-security-gate"
SKILL_NAME = "PR security gate"
SKILL_DESCRIPTION = (
    "Scan a pull request's changed lines with bundled security rules and get a code-computed verdict with "
    "findings, explanations and suggested fixes; or scan a pasted diff, or triage pasted scanner findings. One "
    "summary comment on the PR, posted only after you approve the exact text."
)
SKILL_TAGS = ["security", "pull request", "static analysis", "secrets", "gate", "developer"]
SKILL_EXAMPLES = [
    "Gate PR 482 in acme/api",
    "Check this diff for security issues (paste it)",
    "Triage these Semgrep findings (paste the JSON or SARIF)",
]
