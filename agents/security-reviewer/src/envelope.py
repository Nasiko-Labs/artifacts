"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `findings`, `coverage`).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import severity as sev
from deps import NO_ADVISORY


@dataclass(frozen=True)
class Coverage:
    files_reviewed: tuple[str, ...] = ()
    files_skipped: tuple[dict, ...] = ()
    manifests: tuple[str, ...] = ()
    no_rule_family: tuple[str, ...] = ()
    batches: int = 0
    batches_failed: int = 0
    repaired: bool = False
    focus: str = ""

    @property
    def truncated(self) -> bool:
        return any(s["reason"] == "size cap" for s in self.files_skipped)

    def to_dict(self) -> dict:
        return {"files_reviewed": len(self.files_reviewed), "files": list(self.files_reviewed),
                "files_skipped": [dict(s) for s in self.files_skipped],
                "dependencies_checked": ("structure only; no advisory data" if self.manifests else "none pasted"),
                "no_rule_family": list(self.no_rule_family), "truncated": self.truncated, "focus": self.focus}


@dataclass(frozen=True)
class Review:
    findings: tuple[dict, ...]
    coverage: Coverage
    secrets: int = 0
    dropped: Counter = field(default_factory=Counter)


def confidence(review: Review) -> str:
    cov = review.coverage
    if cov.truncated or cov.batches_failed or cov.no_rule_family:
        return "low"
    only_unverified = bool(review.findings) and all(f["confidence"] == "needs_validation" for f in review.findings)
    if cov.files_skipped or cov.repaired or only_unverified:
        return "medium"
    return "high"


def needs_human(review: Review, level: str) -> bool:
    severe = any(sev.at_or_above(f["severity"], "high") for f in review.findings)
    return severe or review.secrets > 0 or level == "low"


def summary(review: Review) -> str:
    findings = list(review.findings)
    if not findings:
        text = "No findings in the checks I run."
    else:
        counts = sev.counts(findings)
        parts = ", ".join(f"{n} {level}" for level, n in counts.items() if n)
        text = f"{len(findings)} finding{'s' if len(findings) != 1 else ''}: {parts}."
        unverified = sum(1 for f in findings if f["confidence"] == "needs_validation")
        if unverified:
            text += f" {unverified} need{'s' if unverified == 1 else ''} validation."
    if review.secrets:
        text += f" Code was masked; {review.secrets} secret{'s' if review.secrets != 1 else ''} found."
    return text


def build(review: Review) -> dict:
    level = confidence(review)
    items = [_public(f) for f in review.findings]
    return {"summary": summary(review), "items": items, "findings": items, "coverage": review.coverage.to_dict(),
            "confidence": level, "needs_human": needs_human(review, level), "secrets_found": review.secrets,
            "dropped": {k: v for k, v in sorted(review.dropped.items()) if v}, "advisory_data": NO_ADVISORY}


def _public(f: dict) -> dict:
    keep = ("number", "severity", "location", "issue", "impact", "fix", "fix_code", "cwe", "confidence", "evidence",
            "rule", "reachable_from_input", "kind", "test_file")
    return {k: f.get(k) for k in keep}
