"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from dataclasses import dataclass, field

from alerts import FORMAT_NAMES, Parsed
from findings import Change, Finding, counts, noisy

OWNER_CHECK_SHARE = 0.5


@dataclass
class Outcome:
    parsed: Parsed
    findings: list[Finding]
    changes: list[Change]
    labels: dict[str, int]
    label_failed: int = 0           # alerts whose label batch failed (AR008 skipped)
    dropped_findings: int = 0
    dropped_changes: int = 0
    not_checked: list[str] = field(default_factory=list)


def confidence(outcome: Outcome) -> str:
    alerts = outcome.parsed.alerts
    owner_checked = sum(1 for a in alerts if a.vendor in FORMAT_NAMES)
    if outcome.parsed.skipped or (alerts and owner_checked / len(alerts) < OWNER_CHECK_SHARE):
        return "low"
    if outcome.label_failed or outcome.dropped_findings or outcome.dropped_changes:
        return "medium"
    return "high"


def needs_human(outcome: Outcome, level: str) -> bool:
    return level == "low" or any(f.severity == "high" for f in outcome.findings)


def summary(outcome: Outcome) -> str:
    c = counts(outcome.findings)
    formats = ", ".join(FORMAT_NAMES[f] for f in outcome.parsed.formats)
    return (f"Reviewed {len(outcome.parsed.alerts)} alerts ({formats}): {c['high']} high, {c['medium']} medium, "
            f"{c['low']} low findings; {len(outcome.changes)} proposed changes.")


def build(outcome: Outcome) -> dict:
    level = confidence(outcome)
    findings = [f.to_dict() for f in outcome.findings]
    return {
        "summary": summary(outcome),
        "items": findings,
        "findings": findings,
        "proposed_changes": [c.to_dict() for c in outcome.changes],
        "confidence": level,
        "needs_human": needs_human(outcome, level),
        "alerts_reviewed": len(outcome.parsed.alerts),
        "formats": [FORMAT_NAMES[f] for f in outcome.parsed.formats],
        "noisy_by_construction": noisy(outcome.findings),
        "not_checked": list(outcome.not_checked),
        "labels": dict(outcome.labels),
        "dropped": {"findings": outcome.dropped_findings, "proposed_changes": outcome.dropped_changes},
    }
