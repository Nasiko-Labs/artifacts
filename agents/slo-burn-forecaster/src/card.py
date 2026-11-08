"""Card text, shared by __main__.py (the served card) and AgentCard.json (checked by a test).

The description carries the distinction from other agents: workflow generation sees only the name
and description."""

AGENT_NAME = "slo-burn-forecaster"
AGENT_VERSION = "1.0.0"
AGENT_DESCRIPTION = (
    "Forecasts SLO error-budget burn from SLI data you paste (timestamped good/total, errors/total or error-rate "
    "rows per service) and each service's SLO target and period. Computes in code the multi-window burn rates "
    "(1 h, 6 h, 24 h, period), the error budget left, the hours until it runs out at the current rate, a status "
    "per service (fast burn, slow burn, elevated, healthy, exhausted) and which service is burning fastest. "
    "Optionally lists possibly related error-tracker issues and incidents for the top burners. Read-only: it never "
    "queries a metrics system and never claims a number it did not compute."
)

SKILL_ID = "slo-burn-forecast"
SKILL_NAME = "SLO burn forecast"
SKILL_DESCRIPTION = (
    "Ranks services by error-budget burn from pasted SLI rows: burn rate per window, budget remaining, hours to "
    "exhaust, worst 6-hour window and share of errors, with what-if factors and per-service SLO overrides. "
    "All maths in code; read-only."
)
SKILL_TAGS = ["slo", "error budget", "burn rate", "sre", "reliability", "forecast"]
SKILL_EXAMPLES = [
    "Paste checkout and payments error counts and SLOs: which one runs out of budget first?",
    "What if payments' error rate halves?",
    "Why is payments burning?",
]
