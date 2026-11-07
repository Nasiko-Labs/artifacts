"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "security-reviewer"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Reviews code or a diff you paste into the chat for security problems: injection, authentication and "
    "authorization, hardcoded secrets, unsafe crypto, insecure defaults and risky dependencies in pasted "
    "manifests. Rules in code find candidates (about 50 patterns plus a secret scan that masks every secret "
    "before the model sees the code); the model triages each one (reachable from input, impact, fix) and looks "
    "for logic flaws such as missing authorization, IDOR or SSRF. Every finding has a severity from the rule "
    "table, a file:line location, a verbatim quote and a fix; unverified ones are listed as needs validation. "
    "In a diff it reviews the added lines and flags removed checks. Dependency checks are structural only (no "
    "vulnerability database). Read-only: it never runs code, writes exploits or probes systems, and never "
    "calls code secure. Not for general code review, writing code or general security questions."
)

SKILL_ID = "security-review"
SKILL_NAME = "Security review of pasted code"
SKILL_DESCRIPTION = (
    "Security findings for pasted code, a diff or a dependency manifest: severity, file:line, the issue, its "
    "impact and a fix, with secrets masked and a stated coverage."
)
SKILL_TAGS = ["security", "code review", "diff", "secrets", "injection", "authorization", "dependencies", "appsec"]
SKILL_EXAMPLES = [
    "Security review this diff: <paste the diff>",
    "Check this Flask view for injection and auth problems: <paste the code>",
    "Any risky dependencies in this requirements.txt diff? <paste the diff>",
]
