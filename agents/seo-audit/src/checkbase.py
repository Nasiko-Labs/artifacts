"""Shared pieces of the deterministic checks: the per-audit context and the result builder."""

from __future__ import annotations

from dataclasses import dataclass

from checktext import SPECS
from models import CheckResult


@dataclass(frozen=True)
class Context:
    page_url: str = ""            # "" when unknown
    keyword: str = ""             # the user's target keyword ("" when none was given)
    intent: str = ""              # from the quality read ("" when it did not run)
    answer_first: bool | None = None
    robots_txt: str | None = None  # None: robots.txt not read
    llms_found: bool | None = None  # None: llms.txt not read


def result(check_id: str, outcome: str, title: str = "", evidence: str = "", kind: str = "fact",
           reason: str = "", severity: str | None = None) -> CheckResult:
    spec = SPECS[check_id]
    return CheckResult(id=check_id, area=spec.area, severity=severity or spec.severity, weight=spec.weight,
                       outcome=outcome, title=title or spec.name, evidence=evidence, evidence_kind=kind,
                       reason=reason)


def na(check_id: str, reason: str) -> CheckResult:
    return result(check_id, "na", reason=reason)


def ok(check_id: str, evidence: str = "", kind: str = "fact") -> CheckResult:
    return result(check_id, "pass", evidence=evidence, kind=kind)
