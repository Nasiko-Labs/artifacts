"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `title`, `description_markdown`,
`test_plan`, `risk`). `confidence` and `needs_human` are computed here from the run's checks, never taken from
the model."""

from __future__ import annotations

from dataclasses import dataclass

from checks import Report

LOW_DROP_SHARE = 0.3


@dataclass(frozen=True)
class Run:
    """What happened this turn, for confidence."""

    why_given: bool
    grouped: bool = False
    skipped: tuple[str, ...] = ()
    repaired: bool = False
    type_decided: bool = True


def confidence(facts, report: Report, run: Run) -> str:
    dropped_share = report.dropped / report.total if report.total else 0.0
    if facts.stat_only or dropped_share > LOW_DROP_SHARE or not run.type_decided:
        return "low"
    clean = report.dropped == 0 and report.fixes == 0 and not run.repaired
    if run.why_given and clean and not run.grouped and not run.skipped:
        return "high"
    return "medium"


def build(title: str, markdown: str, draft: dict, facts, test_plan: list[str], report: Report, run: Run,
          coverage: list[str]) -> dict:
    level = confidence(facts, report, run)
    risk_notes = [n["text"] for n in draft["risk_notes"]] + [n for n in (facts.size_note, facts.split_note) if n]
    return {
        "summary": f"{title} — {facts.risk} risk, {facts.files} files, +{facts.added} −{facts.removed}",
        "items": [{"kind": "change", "area": c["area"], "text": c["text"], "files": c["files"]}
                  for c in draft["changes"]],
        "title": title,
        "description_markdown": markdown,
        "test_plan": test_plan,
        "risk": {"level": facts.risk, "notes": risk_notes,
                 "surfaces": [{"name": s.name, "token": s.token} for s in facts.surfaces],
                 "size": {"files": facts.files, "added": facts.added, "removed": facts.removed}},
        "confidence": level,
        "needs_human": (not run.why_given) or facts.risk == "high" or level == "low",
        "coverage": coverage,
        "dropped_items": report.dropped,
    }
