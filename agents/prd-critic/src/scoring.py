"""Pure: what each gap costs. A finding is one gap plus its fixed weight and its cap group; the score is 100
minus the capped weights of the deduplicated findings (rubric.evaluate). The weights are fixed per rule, never
per model flag, so the same gap list always gives the same score."""

from __future__ import annotations

import re
from dataclasses import dataclass

SEVERITY_POINTS = {"blocker": 10, "major": 6, "minor": 2}   # a requirement gap, whatever flags made it
REQ_CAP, METRIC_CAP, QUESTION_CAP, PLACEHOLDER_CAP = 30, 20, 10, 10


@dataclass(frozen=True)
class Finding:
    """One gap and what it costs; `group` names its cap (CAPS) and its place in the breakdown."""

    gap: dict
    points: int
    group: str
    reason: str


GROUPS = ("section", "requirement", "few_requirements", "metric", "question", "placeholder", "goal_link")
CAPS = {"requirement": (REQ_CAP, "Requirement"), "metric": (METRIC_CAP, "Metric"),
        "question": (QUESTION_CAP, "Open question"), "placeholder": (PLACEHOLDER_CAP, "Placeholder")}
_REQ_ID = re.compile(r"^R\d+: ")


def deductions_of(findings: list[Finding]) -> list[dict]:
    rows: list[dict] = []
    for group in GROUPS:
        part = [{"points": f.points, "reason": f.reason} for f in findings if f.group == group and f.points]
        total = sum(r["points"] for r in part)
        if group in CAPS and total > CAPS[group][0]:
            cap, label = CAPS[group]
            part.append({"points": cap - total, "reason": f"{label} deductions capped at {cap}"})
        rows += part
    return rows


def _key(f: Finding) -> tuple:
    """Same requirement (or line) with the same issue: one gap."""
    quote = " ".join((f.gap["location"]["quote"] or "").split()).casefold()
    return f.gap["category"], f.gap["location"]["section"], quote, _REQ_ID.sub("", f.gap["issue"]).casefold()


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple] = set()
    out = []
    for f in findings:
        if _key(f) not in seen:
            seen.add(_key(f))
            out.append(f)
    return out
