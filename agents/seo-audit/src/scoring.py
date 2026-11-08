"""Pure: findings, the score, sub-scores, band, fix list and the summary line. All numbers are code.

score = round(100 * passed weight / applicable weight); not-applicable checks are excluded and listed. A
warn earns nothing (it is not a pass). A critical failure (T1, R1) caps the band at "needs work"."""

from __future__ import annotations

from collections import Counter

from checktext import SPECS
from models import SEVERITY_RANK, CheckResult, Finding
from pagetext import verbatim

BANDS = ((90, "strong"), (75, "good"), (50, "needs work"), (0, "weak"))
CAPPING_CHECKS = ("T1", "R1")
FIX_LIST_SIZE = 8


def score_of(checks: list[CheckResult]) -> int:
    applicable = [c for c in checks if c.outcome != "na" and c.weight > 0]
    total = sum(c.weight for c in applicable)
    if total == 0:
        return 0
    return round(100 * sum(c.weight for c in applicable if c.passed) / total)


def band_of(score: int, checks: list[CheckResult]) -> str:
    band = next(name for floor, name in BANDS if score >= floor)
    capped = any(c.id in CAPPING_CHECKS and c.outcome == "fail" for c in checks)
    if capped and band in ("strong", "good"):
        return "needs work"
    return band


def findings(checks: list[CheckResult], *sources: str) -> tuple[list[Finding], int]:
    """(findings for failed/warned checks, number dropped for evidence that is not verbatim)."""
    out: list[Finding] = []
    dropped = 0
    for check in checks:
        if check.outcome not in ("fail", "warn"):
            continue
        if not check.evidence or (check.evidence_kind == "quote" and not verbatim(check.evidence, *sources)):
            dropped += 1
            continue
        out.append(Finding(id="", check=check.id, area=check.area, severity=check.severity, title=check.title,
                           evidence=check.evidence, fix=SPECS[check.id].fix, weight=check.weight))
    ordered = sorted(out, key=lambda f: (-SEVERITY_RANK[f.severity] * f.weight, -SEVERITY_RANK[f.severity], f.check))
    return [f.model_copy(update={"id": f"F{i}"}) for i, f in enumerate(ordered, 1)], dropped


def fix_list(items: list[Finding]) -> list[Finding]:
    """Failed checks by severity rank x weight, ties by check id; info items never appear."""
    scored = [f for f in items if f.severity != "info" and f.weight > 0]
    return sorted(scored, key=lambda f: (-SEVERITY_RANK[f.severity] * f.weight, f.check))[:FIX_LIST_SIZE]


def not_applicable(checks: list[CheckResult]) -> list[str]:
    return [f"{c.id} ({c.reason})" for c in checks if c.outcome == "na"]


def summary(score: int, band: str, items: list[Finding], crawlers: dict[str, str]) -> str:
    counts = Counter(f.severity for f in items if f.severity != "info")
    parts = [f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low") if counts[s]]
    issues = ", ".join(parts) if parts else "no issues found"
    line = f"Score {score}/100 ({band}): {issues}."
    if crawlers:
        blocked = sum(1 for v in crawlers.values() if v.startswith("blocked"))
        line += f" AI crawlers: {blocked} of {len(crawlers)} blocked."
    return line
